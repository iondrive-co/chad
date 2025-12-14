"""Tests for security module."""

from unittest.mock import patch
import pytest
from chad.security import SecurityManager


class TestSecurityManager:
    """Test cases for SecurityManager."""

    def test_hash_password(self):
        """Test password hashing."""
        mgr = SecurityManager()
        password = "testpassword123"
        hashed = mgr.hash_password(password)

        assert isinstance(hashed, str)
        assert len(hashed) > 0
        assert hashed != password

    def test_verify_password_correct(self):
        """Test password verification with correct password."""
        mgr = SecurityManager()
        password = "testpassword123"
        hashed = mgr.hash_password(password)

        assert mgr.verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test password verification with incorrect password."""
        mgr = SecurityManager()
        password = "testpassword123"
        hashed = mgr.hash_password(password)

        assert mgr.verify_password("wrongpassword", hashed) is False

    def test_encrypt_decrypt_value(self):
        """Test encryption and decryption of values."""
        import base64
        import bcrypt

        mgr = SecurityManager()
        password = "masterpassword"
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        salt_bytes = base64.urlsafe_b64decode(salt.encode())
        value = "sk-test-api-key-12345"

        # Encrypt
        encrypted = mgr.encrypt_value(value, password, salt_bytes)
        assert isinstance(encrypted, str)
        assert encrypted != value

        # Decrypt
        decrypted = mgr.decrypt_value(encrypted, password, salt_bytes)
        assert decrypted == value

    def test_decrypt_with_wrong_password_raises_error(self):
        """Test that decryption with wrong password raises an error."""
        import base64
        import bcrypt

        mgr = SecurityManager()
        password = "masterpassword"
        wrong_password = "wrongpassword"
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        salt_bytes = base64.urlsafe_b64decode(salt.encode())
        value = "sk-test-api-key-12345"

        encrypted = mgr.encrypt_value(value, password, salt_bytes)

        with pytest.raises(Exception):
            mgr.decrypt_value(encrypted, wrong_password, salt_bytes)

    def test_load_config_nonexistent_file(self, tmp_path):
        """Test loading config when file doesn't exist."""
        mgr = SecurityManager(tmp_path / "nonexistent.conf")
        config = mgr.load_config()
        assert config == {}

    def test_save_and_load_config(self, tmp_path):
        """Test saving and loading configuration."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        test_config = {
            'password_hash': 'hashed_password',
            'encryption_salt': 'test_salt',
            'api_keys': {'anthropic': 'encrypted_key'}
        }

        mgr.save_config(test_config)

        # Verify file was created and has correct permissions
        assert config_path.exists()
        assert oct(config_path.stat().st_mode)[-3:] == '600'

        # Load and verify
        loaded_config = mgr.load_config()
        assert loaded_config == test_config

    def test_is_first_run_no_config(self, tmp_path):
        """Test is_first_run when no config exists."""
        mgr = SecurityManager(tmp_path / "new.conf")
        assert mgr.is_first_run() is True

    def test_is_first_run_no_password_hash(self, tmp_path):
        """Test is_first_run when config exists but no password hash."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)
        mgr.save_config({'api_keys': {}})

        assert mgr.is_first_run() is True

    def test_is_first_run_with_password_hash(self, tmp_path):
        """Test is_first_run when password hash exists."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)
        mgr.save_config({'password_hash': 'test_hash'})

        assert mgr.is_first_run() is False

    @patch('getpass.getpass')
    def test_setup_main_password(self, mock_getpass, tmp_path):
        """Test main password setup."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        # Mock password input (password, then confirmation)
        mock_getpass.side_effect = ['testpassword123', 'testpassword123']

        password = mgr.setup_main_password()

        assert password == 'testpassword123'
        assert config_path.exists()

        # Verify config was saved correctly
        config = mgr.load_config()
        assert 'password_hash' in config
        assert 'encryption_salt' in config
        assert 'accounts' in config

        # Verify password hash is valid
        assert mgr.verify_password(password, config['password_hash']) is True

    @patch('getpass.getpass')
    def test_setup_main_password_short_allowed(self, mock_getpass, tmp_path):
        """Test main password setup allows short passwords with warning."""
        mgr = SecurityManager(tmp_path / "test.conf")

        # Mock: short password, then confirmation
        mock_getpass.side_effect = ['short', 'short']

        password = mgr.setup_main_password()
        assert password == 'short'

    @patch('getpass.getpass')
    def test_setup_main_password_empty_allowed(self, mock_getpass, tmp_path):
        """Test main password setup allows empty password with warning."""
        mgr = SecurityManager(tmp_path / "test.conf")

        # Mock: empty password, then confirmation
        mock_getpass.side_effect = ['', '']

        password = mgr.setup_main_password()
        assert password == ''

    @patch('getpass.getpass')
    def test_setup_main_password_mismatch(self, mock_getpass, tmp_path):
        """Test main password setup with mismatched passwords."""
        mgr = SecurityManager(tmp_path / "test.conf")

        # Mock: password, wrong confirmation, then correct pair
        mock_getpass.side_effect = [
            'testpassword123', 'wrongconfirm',
            'testpassword123', 'testpassword123'
        ]

        password = mgr.setup_main_password()
        assert password == 'testpassword123'

    @patch('getpass.getpass')
    def test_verify_main_password_success(self, mock_getpass, tmp_path):
        """Test successful main password verification."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        # Setup password first
        password = 'testpassword123'
        password_hash = mgr.hash_password(password)
        mgr.save_config({'password_hash': password_hash})

        # Mock getpass to return correct password
        mock_getpass.return_value = password

        verified_password = mgr.verify_main_password()
        assert verified_password == password

    @patch('getpass.getpass')
    def test_verify_main_password_retry(self, mock_getpass, tmp_path):
        """Test main password verification with retry."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        password = 'testpassword123'
        password_hash = mgr.hash_password(password)
        mgr.save_config({'password_hash': password_hash})

        # Mock: wrong password (triggers reset prompt), cancel with EOFError, then correct password
        mock_getpass.side_effect = ['wrongpassword', EOFError(), password]

        verified_password = mgr.verify_main_password()
        assert verified_password == password

    @patch('getpass.getpass')
    def test_verify_main_password_retry_after_decline(self, mock_getpass, tmp_path):
        """Test main password verification retries after cancelling reset."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        password = 'correct123'
        password_hash = mgr.hash_password(password)
        mgr.save_config({'password_hash': password_hash})

        # Mock: wrong password, cancel reset, wrong password again, cancel reset, then correct password
        mock_getpass.side_effect = ['wrong1', EOFError(), 'wrong2', KeyboardInterrupt(), password]

        verified = mgr.verify_main_password()
        assert verified == password

    @patch('getpass.getpass')
    def test_verify_main_password_reset_accepted(self, mock_getpass, tmp_path):
        """Test main password reset when user accepts."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        # Setup old password and accounts
        old_password = 'oldpassword'
        password_hash = mgr.hash_password(old_password)
        mgr.save_config({
            'password_hash': password_hash,
            'accounts': {'test-account': {'provider': 'anthropic', 'key': 'encrypted'}}
        })

        # Mock: wrong password (becomes new password candidate), then confirmation
        mock_getpass.side_effect = [
            'newpassword',  # Wrong password (becomes new password)
            'newpassword'   # Confirmation
        ]

        new_password = mgr.verify_main_password()

        assert new_password == 'newpassword'
        # Verify old accounts were deleted
        config = mgr.load_config()
        assert 'accounts' in config
        assert len(config['accounts']) == 0

    @patch('getpass.getpass')
    def test_verify_main_password_reset_confirmation_failed_then_retry(self, mock_getpass, tmp_path):
        """Test main password reset with mismatched confirmation, then retry and succeed."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        old_password = 'correct123'
        password_hash = mgr.hash_password(old_password)
        mgr.save_config({'password_hash': password_hash})

        # Mock: wrong password, mismatched confirmation, wrong password again, matching confirmation
        mock_getpass.side_effect = [
            'newpass',    # Wrong password (becomes new password candidate)
            'different',  # Mismatched confirmation - loops back
            'newpass2',   # Wrong password again (becomes new password candidate)
            'newpass2'    # Matching confirmation
        ]

        verified = mgr.verify_main_password()
        assert verified == 'newpass2'

    def test_store_and_get_api_key(self, tmp_path):
        """Test storing and retrieving API keys."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        # Setup master password
        import base64
        import bcrypt
        password = 'masterpassword'
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt,
            'api_keys': {}
        })

        # Store API key
        api_key = 'sk-test-anthropic-key-12345'
        mgr.store_api_key('anthropic', api_key, password)

        # Retrieve API key
        retrieved_key = mgr.get_api_key('anthropic', password)
        assert retrieved_key == api_key

    def test_get_api_key_nonexistent(self, tmp_path):
        """Test retrieving non-existent API key."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        import base64
        import bcrypt
        password = 'masterpassword'
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({
            'encryption_salt': salt,
            'api_keys': {}
        })

        retrieved_key = mgr.get_api_key('nonexistent', password)
        assert retrieved_key is None

    def test_has_api_key(self, tmp_path):
        """Test checking if API key exists."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        mgr.save_config({
            'api_keys': {'anthropic': 'encrypted_key'}
        })

        assert mgr.has_api_key('anthropic') is True
        assert mgr.has_api_key('openai') is False

    def test_list_stored_providers(self, tmp_path):
        """Test listing stored providers."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        mgr.save_config({
            'api_keys': {
                'anthropic': 'key1',
                'openai': 'key2'
            }
        })

        providers = mgr.list_stored_providers()
        assert set(providers) == {'anthropic', 'openai'}

    def test_list_stored_providers_empty(self, tmp_path):
        """Test listing providers when none are stored."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)
        mgr.save_config({})

        providers = mgr.list_stored_providers()
        assert providers == []

    def test_store_and_get_account(self, tmp_path):
        """Test storing and retrieving named accounts."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        # Setup main password
        import base64
        import bcrypt
        password = 'testpassword'
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt,
            'accounts': {}
        })

        # Store account
        api_key = 'sk-test-key-12345'
        mgr.store_account('work-anthropic', 'anthropic', api_key, password)

        # Retrieve account
        account = mgr.get_account('work-anthropic', password)
        assert account is not None
        assert account['provider'] == 'anthropic'
        assert account['api_key'] == api_key

    def test_list_accounts(self, tmp_path):
        """Test listing all accounts."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        import base64
        import bcrypt
        password = 'testpassword'
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt,
            'accounts': {}
        })

        # Store multiple accounts
        mgr.store_account('work-anthropic', 'anthropic', 'key1', password)
        mgr.store_account('personal-openai', 'openai', 'key2', password)

        # List accounts
        accounts = mgr.list_accounts()
        assert len(accounts) == 2
        assert accounts['work-anthropic'] == 'anthropic'
        assert accounts['personal-openai'] == 'openai'

    def test_has_account(self, tmp_path):
        """Test checking if account exists."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        import base64
        import bcrypt
        password = 'testpassword'
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt,
            'accounts': {}
        })

        mgr.store_account('my-account', 'anthropic', 'key', password)

        assert mgr.has_account('my-account') is True
        assert mgr.has_account('nonexistent') is False

    def test_backward_compatibility_with_old_format(self, tmp_path):
        """Test that old api_keys format is still readable."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        import base64
        import bcrypt
        password = 'testpassword'
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        salt_bytes = base64.urlsafe_b64decode(salt.encode())

        # Create old format config
        api_key = 'sk-old-key'
        encrypted_key = mgr.encrypt_value(api_key, password, salt_bytes)

        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt,
            'api_keys': {
                'anthropic': encrypted_key
            }
        })

        # Should be able to read with new methods
        accounts = mgr.list_accounts()
        assert 'anthropic' in accounts
        assert accounts['anthropic'] == 'anthropic'

        account = mgr.get_account('anthropic', password)
        assert account is not None
        assert account['provider'] == 'anthropic'
        assert account['api_key'] == api_key

    def test_encrypt_value_produces_different_output_each_time(self, tmp_path):
        """Test that encrypt_value produces different output each time due to IV."""
        import bcrypt

        mgr = SecurityManager(tmp_path / "test.conf")
        password = "testpassword"
        salt_bytes = bcrypt.gensalt()
        value = "same-test-value"

        encrypted1 = mgr.encrypt_value(value, password, salt_bytes)
        encrypted2 = mgr.encrypt_value(value, password, salt_bytes)

        # Should produce different encrypted values due to random IV
        assert encrypted1 != encrypted2

        # But both should decrypt to the same value
        decrypted1 = mgr.decrypt_value(encrypted1, password, salt_bytes)
        decrypted2 = mgr.decrypt_value(encrypted2, password, salt_bytes)
        assert decrypted1 == value
        assert decrypted2 == value

    def test_encrypt_decrypt_empty_string(self, tmp_path):
        """Test encryption and decryption of empty string."""
        import bcrypt

        mgr = SecurityManager(tmp_path / "test.conf")
        password = "testpassword"
        salt_bytes = bcrypt.gensalt()
        value = ""

        encrypted = mgr.encrypt_value(value, password, salt_bytes)
        decrypted = mgr.decrypt_value(encrypted, password, salt_bytes)
        assert decrypted == value

    def test_encrypt_decrypt_very_long_value(self, tmp_path):
        """Test encryption and decryption of very long API key."""
        import bcrypt

        mgr = SecurityManager(tmp_path / "test.conf")
        password = "testpassword"
        salt_bytes = bcrypt.gensalt()
        # Create a very long value (10KB)
        value = "sk-" + "x" * 10000

        encrypted = mgr.encrypt_value(value, password, salt_bytes)
        decrypted = mgr.decrypt_value(encrypted, password, salt_bytes)
        assert decrypted == value

    def test_decrypt_value_with_truncated_encrypted_value(self, tmp_path):
        """Test that decrypt_value fails gracefully with corrupted encrypted data."""
        import bcrypt

        mgr = SecurityManager(tmp_path / "test.conf")
        password = "testpassword"
        salt_bytes = bcrypt.gensalt()
        value = "test-api-key"

        encrypted = mgr.encrypt_value(value, password, salt_bytes)
        # Truncate the encrypted value to corrupt it
        corrupted = encrypted[:len(encrypted)//2]

        with pytest.raises(Exception):  # Should raise some form of decryption error
            mgr.decrypt_value(corrupted, password, salt_bytes)

    def test_get_role_assignment_nonexistent_role(self, tmp_path):
        """Test get_role_assignment for role that was never assigned."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        result = mgr.get_role_assignment('NONEXISTENT_ROLE')
        assert result is None

    def test_list_role_assignments_empty(self, tmp_path):
        """Test list_role_assignments when no roles are assigned."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        assignments = mgr.list_role_assignments()
        assert assignments == {}

    def test_assign_role_overwrites_previous_role(self, tmp_path):
        """assign_role should overwrite an existing assignment for the role."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)
        password = "testpassword"

        import base64
        import bcrypt
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt
        })

        mgr.store_account('account1', 'anthropic', 'key1', password, 'model1')
        mgr.store_account('account2', 'openai', 'key2', password, 'model2')

        mgr.assign_role('account1', 'CODING')
        assert mgr.get_role_assignment('CODING') == 'account1'

        mgr.assign_role('account2', 'CODING')
        assert mgr.get_role_assignment('CODING') == 'account2'

    def test_assign_role_to_account_that_doesnt_exist(self, tmp_path):
        """assign_role should error when the account does not exist."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        with pytest.raises(ValueError):
            mgr.assign_role('nonexistent', 'CODING')

    def test_clear_role_nonexistent(self, tmp_path):
        """Clearing a missing role should be a no-op."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        mgr.clear_role('NONEXISTENT_ROLE')
        assert mgr.get_role_assignment('NONEXISTENT_ROLE') is None

    def test_delete_account_cascades_to_role_assignments(self, tmp_path):
        """delete_account should remove related role assignments."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)
        password = "testpassword"

        import base64
        import bcrypt
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt
        })

        mgr.store_account('test_account', 'anthropic', 'key', password, 'model')
        mgr.assign_role('test_account', 'CODING')
        assert mgr.get_role_assignment('CODING') == 'test_account'

        mgr.delete_account('test_account')

        assert mgr.get_role_assignment('CODING') is None
        assert 'test_account' not in mgr.list_accounts()

    def test_delete_account_nonexistent(self, tmp_path):
        """Deleting a missing account should not error."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        mgr.delete_account('nonexistent')

    def test_save_and_load_preferences(self, tmp_path):
        """Test saving and loading user preferences."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        mgr.save_preferences("/tmp/project-path")
        loaded_prefs = mgr.load_preferences()
        assert loaded_prefs == {'project_path': "/tmp/project-path"}

    def test_load_preferences_nonexistent(self, tmp_path):
        """Test loading preferences when none have been saved."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        preferences = mgr.load_preferences()
        assert preferences is None

    def test_mixed_old_and_new_format_accounts(self, tmp_path):
        """Test config with both 'api_keys' and 'accounts' sections."""
        config_path = tmp_path / "test.conf"
        mgr = SecurityManager(config_path)

        import base64
        import bcrypt
        password = 'testpassword'
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        salt_bytes = base64.urlsafe_b64decode(salt.encode())

        # Create mixed format config
        old_key = mgr.encrypt_value('old-api-key', password, salt_bytes)
        new_account_key = mgr.encrypt_value('new-api-key', password, salt_bytes)

        mgr.save_config({
            'password_hash': password_hash,
            'encryption_salt': salt,
            'api_keys': {  # Old format
                'anthropic': old_key
            },
            'accounts': {  # New format
                'new_account': {
                    'provider': 'openai',
                    'encrypted_api_key': new_account_key,
                    'model': 'gpt-4'
                }
            }
        })

        accounts = mgr.list_accounts()
        assert 'anthropic' in accounts  # From old format
        assert 'new_account' in accounts  # From new format
        assert len(accounts) == 2  # No duplicates
