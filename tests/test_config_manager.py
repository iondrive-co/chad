"""Tests for config manager module."""

from unittest.mock import patch
import os
import pytest
from chad.util.config_manager import CONFIG_BASE_KEYS, ConfigManager, validate_config_keys


class TestConfigManager:
    """Test cases for ConfigManager."""

    def test_hash_password(self):
        """Test password hashing."""
        mgr = ConfigManager()
        password = "testpassword123"
        hashed = mgr.hash_password(password)

        assert isinstance(hashed, str)
        assert len(hashed) > 0
        assert hashed != password

    def test_verify_password_correct(self):
        """Test password verification with correct password."""
        mgr = ConfigManager()
        password = "testpassword123"
        hashed = mgr.hash_password(password)

        assert mgr.verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test password verification with incorrect password."""
        mgr = ConfigManager()
        password = "testpassword123"
        hashed = mgr.hash_password(password)

        assert mgr.verify_password("wrongpassword", hashed) is False

    def test_encrypt_decrypt_value(self):
        """Test encryption and decryption of values."""
        import base64
        import bcrypt

        mgr = ConfigManager()
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

        mgr = ConfigManager()
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
        mgr = ConfigManager(tmp_path / "nonexistent.conf")
        config = mgr.load_config()
        assert config == {}

    def test_save_and_load_config(self, tmp_path):
        """Test saving and loading configuration."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        test_config = {
            "password_hash": "hashed_password",
            "encryption_salt": "test_salt",
            "api_keys": {"anthropic": "encrypted_key"},
        }

        mgr.save_config(test_config)

        # Verify file was created and has correct permissions
        assert config_path.exists()
        expected_mode = "666" if os.name == "nt" else "600"
        assert oct(config_path.stat().st_mode)[-3:] == expected_mode

        # Load and verify
        loaded_config = mgr.load_config()
        assert loaded_config == test_config

    def test_is_first_run_no_config(self, tmp_path):
        """Test is_first_run when no config exists."""
        mgr = ConfigManager(tmp_path / "new.conf")
        assert mgr.is_first_run() is True

    def test_is_first_run_no_password_hash(self, tmp_path):
        """Test is_first_run when config exists but no password hash."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)
        mgr.save_config({"api_keys": {}})

        assert mgr.is_first_run() is True

    def test_is_first_run_with_password_hash(self, tmp_path):
        """Test is_first_run when password hash exists."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)
        mgr.save_config({"password_hash": "test_hash"})

        assert mgr.is_first_run() is False

    @patch("getpass.getpass")
    def test_setup_main_password(self, mock_getpass, tmp_path):
        """Test main password setup."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Mock password input (password, then confirmation)
        mock_getpass.side_effect = ["testpassword123", "testpassword123"]

        password = mgr.setup_main_password()

        assert password == "testpassword123"
        assert config_path.exists()

        # Verify config was saved correctly
        config = mgr.load_config()
        assert "password_hash" in config
        assert "encryption_salt" in config
        assert "accounts" in config

        # Verify password hash is valid
        assert mgr.verify_password(password, config["password_hash"]) is True

    @patch("getpass.getpass")
    def test_setup_main_password_short_allowed(self, mock_getpass, tmp_path):
        """Test main password setup allows short passwords with warning."""
        mgr = ConfigManager(tmp_path / "test.conf")

        # Mock: short password, then confirmation
        mock_getpass.side_effect = ["short", "short"]

        password = mgr.setup_main_password()
        assert password == "short"

    @patch("getpass.getpass")
    def test_setup_main_password_empty_allowed(self, mock_getpass, tmp_path):
        """Test main password setup allows empty password with warning."""
        mgr = ConfigManager(tmp_path / "test.conf")

        # Mock: empty password, then confirmation
        mock_getpass.side_effect = ["", ""]

        password = mgr.setup_main_password()
        assert password == ""

    @patch("getpass.getpass")
    def test_setup_main_password_mismatch(self, mock_getpass, tmp_path):
        """Test main password setup with mismatched passwords."""
        mgr = ConfigManager(tmp_path / "test.conf")

        # Mock: password, wrong confirmation, then correct pair
        mock_getpass.side_effect = ["testpassword123", "wrongconfirm", "testpassword123", "testpassword123"]

        password = mgr.setup_main_password()
        assert password == "testpassword123"

    @patch("getpass.getpass")
    def test_verify_main_password_success(self, mock_getpass, tmp_path):
        """Test successful main password verification."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Setup password first
        password = "testpassword123"
        password_hash = mgr.hash_password(password)
        mgr.save_config({"password_hash": password_hash})

        # Mock getpass to return correct password
        mock_getpass.return_value = password

        verified_password = mgr.verify_main_password()
        assert verified_password == password

    @patch("getpass.getpass")
    def test_verify_main_password_retry(self, mock_getpass, tmp_path):
        """Test main password verification with retry."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        password = "testpassword123"
        password_hash = mgr.hash_password(password)
        mgr.save_config({"password_hash": password_hash})

        # Mock: wrong password (triggers reset prompt), cancel with EOFError, then correct password
        mock_getpass.side_effect = ["wrongpassword", EOFError(), password]

        verified_password = mgr.verify_main_password()
        assert verified_password == password

    @patch("getpass.getpass")
    def test_verify_main_password_retry_after_decline(self, mock_getpass, tmp_path):
        """Test main password verification retries after cancelling reset."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        password = "correct123"
        password_hash = mgr.hash_password(password)
        mgr.save_config({"password_hash": password_hash})

        # Mock: wrong password, cancel reset, wrong password again, cancel reset, then correct password
        mock_getpass.side_effect = ["wrong1", EOFError(), "wrong2", KeyboardInterrupt(), password]

        verified = mgr.verify_main_password()
        assert verified == password

    @patch("getpass.getpass")
    def test_verify_main_password_reset_accepted(self, mock_getpass, tmp_path):
        """Test main password reset when user accepts."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Setup old password and accounts
        old_password = "oldpassword"
        password_hash = mgr.hash_password(old_password)
        mgr.save_config(
            {
                "password_hash": password_hash,
                "accounts": {"test-account": {"provider": "anthropic", "key": "encrypted"}},
            }
        )

        # Mock: wrong password (becomes new password candidate), then confirmation
        mock_getpass.side_effect = [
            "newpassword",  # Wrong password (becomes new password)
            "newpassword",  # Confirmation
        ]

        new_password = mgr.verify_main_password()

        assert new_password == "newpassword"
        # Verify old accounts were deleted
        config = mgr.load_config()
        assert "accounts" in config
        assert len(config["accounts"]) == 0

    @patch("getpass.getpass")
    def test_verify_main_password_reset_confirmation_failed_then_retry(self, mock_getpass, tmp_path):
        """Test main password reset with mismatched confirmation, then retry and succeed."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        old_password = "correct123"
        password_hash = mgr.hash_password(old_password)
        mgr.save_config({"password_hash": password_hash})

        # Mock: wrong password, mismatched confirmation, wrong password again, matching confirmation
        mock_getpass.side_effect = [
            "newpass",  # Wrong password (becomes new password candidate)
            "different",  # Mismatched confirmation - loops back
            "newpass2",  # Wrong password again (becomes new password candidate)
            "newpass2",  # Matching confirmation
        ]

        verified = mgr.verify_main_password()
        assert verified == "newpass2"

    def test_store_and_get_account(self, tmp_path):
        """Test storing and retrieving named accounts."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Setup main password
        import base64
        import bcrypt

        password = "testpassword"
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({"password_hash": password_hash, "encryption_salt": salt, "accounts": {}})

        # Store account
        api_key = "sk-test-key-12345"
        mgr.store_account("work-anthropic", "anthropic", api_key, password)

        # Retrieve account
        account = mgr.get_account("work-anthropic", password)
        assert account is not None
        assert account["provider"] == "anthropic"
        assert account["api_key"] == api_key

    def test_list_accounts(self, tmp_path):
        """Test listing all accounts."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        import base64
        import bcrypt

        password = "testpassword"
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({"password_hash": password_hash, "encryption_salt": salt, "accounts": {}})

        # Store multiple accounts
        mgr.store_account("work-anthropic", "anthropic", "key1", password)
        mgr.store_account("personal-openai", "openai", "key2", password)

        # List accounts
        accounts = mgr.list_accounts()
        assert len(accounts) == 2
        assert accounts["work-anthropic"] == "anthropic"
        assert accounts["personal-openai"] == "openai"

    def test_has_account(self, tmp_path):
        """Test checking if account exists."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        import base64
        import bcrypt

        password = "testpassword"
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        mgr.save_config({"password_hash": password_hash, "encryption_salt": salt, "accounts": {}})

        mgr.store_account("my-account", "anthropic", "key", password)

        assert mgr.has_account("my-account") is True
        assert mgr.has_account("nonexistent") is False

    def test_encrypt_value_produces_different_output_each_time(self, tmp_path):
        """Test that encrypt_value produces different output each time due to IV."""
        import bcrypt

        mgr = ConfigManager(tmp_path / "test.conf")
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

        mgr = ConfigManager(tmp_path / "test.conf")
        password = "testpassword"
        salt_bytes = bcrypt.gensalt()
        value = ""

        encrypted = mgr.encrypt_value(value, password, salt_bytes)
        decrypted = mgr.decrypt_value(encrypted, password, salt_bytes)
        assert decrypted == value

    def test_encrypt_decrypt_very_long_value(self, tmp_path):
        """Test encryption and decryption of very long API key."""
        import bcrypt

        mgr = ConfigManager(tmp_path / "test.conf")
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

        mgr = ConfigManager(tmp_path / "test.conf")
        password = "testpassword"
        salt_bytes = bcrypt.gensalt()
        value = "test-api-key"

        encrypted = mgr.encrypt_value(value, password, salt_bytes)
        # Truncate the encrypted value to corrupt it
        corrupted = encrypted[: len(encrypted) // 2]

        with pytest.raises(Exception):  # Should raise some form of decryption error
            mgr.decrypt_value(corrupted, password, salt_bytes)

    def test_get_role_assignment_nonexistent_role(self, tmp_path):
        """Test get_role_assignment for role that was never assigned."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        result = mgr.get_role_assignment("NONEXISTENT_ROLE")
        assert result is None

    def test_list_role_assignments_empty(self, tmp_path):
        """Test list_role_assignments when no roles are assigned."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        assignments = mgr.list_role_assignments()
        assert assignments == {}

    def test_assign_role_overwrites_previous_role(self, tmp_path):
        """assign_role should overwrite an existing assignment for the role."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)
        password = "testpassword"

        import base64
        import bcrypt

        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        mgr.save_config({"password_hash": password_hash, "encryption_salt": salt})

        mgr.store_account("account1", "anthropic", "key1", password, "model1")
        mgr.store_account("account2", "openai", "key2", password, "model2")

        mgr.assign_role("account1", "CODING")
        assert mgr.get_role_assignment("CODING") == "account1"

        mgr.assign_role("account2", "CODING")
        assert mgr.get_role_assignment("CODING") == "account2"

    def test_assign_role_to_account_that_doesnt_exist(self, tmp_path):
        """assign_role should error when the account does not exist."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        with pytest.raises(ValueError):
            mgr.assign_role("nonexistent", "CODING")

    def test_clear_role_nonexistent(self, tmp_path):
        """Clearing a missing role should be a no-op."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.clear_role("NONEXISTENT_ROLE")
        assert mgr.get_role_assignment("NONEXISTENT_ROLE") is None

    def test_delete_account_cascades_to_role_assignments(self, tmp_path):
        """delete_account should remove related role assignments."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)
        password = "testpassword"

        import base64
        import bcrypt

        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        mgr.save_config({"password_hash": password_hash, "encryption_salt": salt})

        mgr.store_account("test_account", "anthropic", "key", password, "model")
        mgr.assign_role("test_account", "CODING")
        assert mgr.get_role_assignment("CODING") == "test_account"

        mgr.delete_account("test_account")

        assert mgr.get_role_assignment("CODING") is None
        assert "test_account" not in mgr.list_accounts()

    def test_delete_account_nonexistent(self, tmp_path):
        """Deleting a missing account should not error."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.delete_account("nonexistent")

    def test_save_and_load_preferences(self, tmp_path):
        """Test saving and loading user preferences."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.save_preferences("/tmp/project-path")
        loaded_prefs = mgr.load_preferences()
        assert loaded_prefs == {"project_path": "/tmp/project-path"}

    def test_load_preferences_nonexistent(self, tmp_path):
        """Test loading preferences when none have been saved."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        preferences = mgr.load_preferences()
        assert preferences is None

    def test_multiple_accounts(self, tmp_path):
        """Test config with multiple accounts."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        import base64
        import bcrypt

        password = "testpassword"
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        salt_bytes = base64.urlsafe_b64decode(salt.encode())

        # Create config with multiple accounts
        account1_key = mgr.encrypt_value("api-key-1", password, salt_bytes)
        account2_key = mgr.encrypt_value("api-key-2", password, salt_bytes)

        mgr.save_config(
            {
                "password_hash": password_hash,
                "encryption_salt": salt,
                "accounts": {
                    "work-anthropic": {"provider": "anthropic", "key": account1_key, "model": "claude"},
                    "personal-openai": {"provider": "openai", "key": account2_key, "model": "gpt-4"},
                },
            }
        )

        accounts = mgr.list_accounts()
        assert "work-anthropic" in accounts
        assert "personal-openai" in accounts
        assert accounts["work-anthropic"] == "anthropic"
        assert accounts["personal-openai"] == "openai"
        assert len(accounts) == 2  # No duplicates

    def test_validate_config_keys_accepts_known_keys(self):
        """validate_config_keys should allow all base keys."""
        config = {key: "value" for key in CONFIG_BASE_KEYS}
        validate_config_keys(config)  # Should not raise

    def test_validate_config_keys_rejects_unknown(self):
        """validate_config_keys should force panel updates for new keys."""
        config = {"password_hash": "hash", "unexpected": True}
        with pytest.raises(ValueError):
            validate_config_keys(config)


class TestVerificationAgent:
    """Tests for verification agent configuration."""

    def test_set_verification_agent_none_marker(self, tmp_path):
        """Setting verification agent to VERIFICATION_NONE stores the marker."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Set the verification agent to the VERIFICATION_NONE marker
        mgr.set_verification_agent(mgr.VERIFICATION_NONE)

        # Verify the marker is stored in the config
        config = mgr.load_config()
        assert config.get("verification_agent") == "__verification_none__"

    def test_get_verification_agent_returns_none_marker(self, tmp_path):
        """Getting verification agent returns VERIFICATION_NONE marker when stored."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Set the verification agent to the VERIFICATION_NONE marker
        mgr.set_verification_agent(mgr.VERIFICATION_NONE)

        # Verify get returns the marker (not None, not empty string)
        result = mgr.get_verification_agent()
        assert result == mgr.VERIFICATION_NONE
        assert result == "__verification_none__"

    def test_verification_agent_none_marker_persists_across_instances(self, tmp_path):
        """VERIFICATION_NONE marker persists and can be retrieved by a new ConfigManager."""
        config_path = tmp_path / "test.conf"

        # Set verification agent with first instance
        mgr1 = ConfigManager(config_path)
        mgr1.set_verification_agent(mgr1.VERIFICATION_NONE)

        # Create a new instance (simulating restart) and verify
        mgr2 = ConfigManager(config_path)
        result = mgr2.get_verification_agent()

        assert result == mgr2.VERIFICATION_NONE
        assert result == "__verification_none__"

    def test_set_verification_agent_to_none_clears_setting(self, tmp_path):
        """Setting verification agent to None (not the marker) clears the setting."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # First set to the VERIFICATION_NONE marker
        mgr.set_verification_agent(mgr.VERIFICATION_NONE)
        assert mgr.get_verification_agent() == mgr.VERIFICATION_NONE

        # Then clear by setting to None
        mgr.set_verification_agent(None)

        # Should return None (not the marker)
        result = mgr.get_verification_agent()
        assert result is None

        # Config should not have the key
        config = mgr.load_config()
        assert "verification_agent" not in config


class TestPreferredVerificationModel:
    """Tests for preferred verification model configuration."""

    def test_set_and_get_preferred_verification_model(self, tmp_path):
        """Test setting and getting preferred verification model."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Initially should return None
        assert mgr.get_preferred_verification_model() is None

        # Set a model
        mgr.set_preferred_verification_model("claude-3-5-sonnet-20241022")

        # Should return the set value
        result = mgr.get_preferred_verification_model()
        assert result == "claude-3-5-sonnet-20241022"

        # Verify it's stored in config
        config = mgr.load_config()
        assert config.get("preferred_verification_model") == "claude-3-5-sonnet-20241022"

    def test_set_preferred_verification_model_to_none_clears_setting(self, tmp_path):
        """Setting preferred verification model to None clears the setting."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # First set a model
        mgr.set_preferred_verification_model("gpt-4o")
        assert mgr.get_preferred_verification_model() == "gpt-4o"

        # Clear by setting to None
        mgr.set_preferred_verification_model(None)

        # Should return None
        result = mgr.get_preferred_verification_model()
        assert result is None

        # Config should not have the key
        config = mgr.load_config()
        assert "preferred_verification_model" not in config

    def test_preferred_verification_model_persists_across_instances(self, tmp_path):
        """Preferred verification model persists and can be retrieved by a new ConfigManager."""
        config_path = tmp_path / "test.conf"

        # Set model with first instance
        mgr1 = ConfigManager(config_path)
        mgr1.set_preferred_verification_model("gemini-2.0-flash-exp")

        # Create a new instance (simulating restart) and verify
        mgr2 = ConfigManager(config_path)
        result = mgr2.get_preferred_verification_model()

        assert result == "gemini-2.0-flash-exp"

    def test_preferred_verification_model_independent_of_account_model(self, tmp_path):
        """Preferred verification model is stored separately from account model."""
        import base64
        import bcrypt

        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Setup account with password
        password = "testpassword"
        password_hash = mgr.hash_password(password)
        salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        mgr.save_config({"password_hash": password_hash, "encryption_salt": salt})

        # Store account with a model
        mgr.store_account("test-account", "anthropic", "key", password, "claude-opus-4-20250514")
        mgr.set_verification_agent("test-account")

        # Set a different preferred verification model
        mgr.set_preferred_verification_model("claude-3-5-sonnet-20241022")

        # They should be independent
        assert mgr.get_account_model("test-account") == "claude-opus-4-20250514"
        assert mgr.get_preferred_verification_model() == "claude-3-5-sonnet-20241022"


class TestProjectConfig:
    """Tests for per-project configuration."""

    def test_get_project_config_nonexistent(self, tmp_path):
        """Test getting config for a project that doesn't exist."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        result = mgr.get_project_config("/some/project/path")
        assert result is None

    def test_set_and_get_project_config(self, tmp_path):
        """Test setting and getting project config."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        project_path = tmp_path / "my_project"
        project_path.mkdir()

        project_config = {
            "version": "1.0",
            "project_type": "python",
            "verification": {
                "lint_command": "flake8 .",
                "test_command": "pytest tests/",
                "validated": True,
            },
        }

        mgr.set_project_config(project_path, project_config)

        result = mgr.get_project_config(project_path)
        assert result is not None
        assert result["project_type"] == "python"
        assert result["verification"]["lint_command"] == "flake8 ."

    def test_project_config_normalizes_path(self, tmp_path):
        """Test that project config normalizes paths to absolute."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        project_path = tmp_path / "my_project"
        project_path.mkdir()

        project_config = {"project_type": "javascript"}

        # Save with one form of the path
        mgr.set_project_config(str(project_path), project_config)

        # Retrieve with another form (Path object)
        result = mgr.get_project_config(project_path)
        assert result is not None
        assert result["project_type"] == "javascript"

    def test_delete_project_config(self, tmp_path):
        """Test deleting project config."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        project_path = tmp_path / "my_project"
        project_path.mkdir()

        mgr.set_project_config(project_path, {"project_type": "rust"})
        assert mgr.get_project_config(project_path) is not None

        mgr.delete_project_config(project_path)
        assert mgr.get_project_config(project_path) is None

    def test_delete_project_config_nonexistent(self, tmp_path):
        """Test deleting nonexistent project config is a no-op."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Should not raise
        mgr.delete_project_config("/nonexistent/path")

    def test_list_project_configs(self, tmp_path):
        """Test listing all project configs."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        project1 = tmp_path / "project1"
        project1.mkdir()
        project2 = tmp_path / "project2"
        project2.mkdir()

        mgr.set_project_config(project1, {"project_type": "python"})
        mgr.set_project_config(project2, {"project_type": "rust"})

        configs = mgr.list_project_configs()
        assert len(configs) == 2
        assert configs[str(project1.resolve())]["project_type"] == "python"
        assert configs[str(project2.resolve())]["project_type"] == "rust"

    def test_list_project_configs_empty(self, tmp_path):
        """Test listing project configs when none exist."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        configs = mgr.list_project_configs()
        assert configs == {}

    def test_project_config_persists_across_instances(self, tmp_path):
        """Test that project config persists across ConfigManager instances."""
        config_path = tmp_path / "test.conf"
        project_path = tmp_path / "my_project"
        project_path.mkdir()

        # Set with first instance
        mgr1 = ConfigManager(config_path)
        mgr1.set_project_config(project_path, {"project_type": "go"})

        # Get with second instance
        mgr2 = ConfigManager(config_path)
        result = mgr2.get_project_config(project_path)

        assert result is not None
        assert result["project_type"] == "go"


class TestProviderFallbackOrder:
    """Test cases for provider fallback order configuration."""

    def test_get_fallback_order_empty(self, tmp_path):
        """Test getting fallback order when none is set."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        order = mgr.get_provider_fallback_order()
        assert order == []

    def test_set_and_get_fallback_order(self, tmp_path):
        """Test setting and getting fallback order."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Need to set up accounts first
        mgr.save_config({
            "password_hash": mgr.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {
                "work-claude": {"provider": "anthropic", "key": "xxx"},
                "personal-gpt": {"provider": "openai", "key": "xxx"},
                "backup-gemini": {"provider": "gemini", "key": "xxx"},
            },
        })

        mgr.set_provider_fallback_order(["work-claude", "personal-gpt", "backup-gemini"])
        order = mgr.get_provider_fallback_order()

        assert order == ["work-claude", "personal-gpt", "backup-gemini"]

    def test_fallback_order_filters_invalid_accounts(self, tmp_path):
        """Test that fallback order filters out accounts that no longer exist."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Set up with some accounts
        mgr.save_config({
            "password_hash": mgr.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {
                "work-claude": {"provider": "anthropic", "key": "xxx"},
            },
            "provider_fallback_order": ["work-claude", "deleted-account", "also-deleted"],
        })

        order = mgr.get_provider_fallback_order()
        assert order == ["work-claude"]

    def test_set_fallback_order_validates_accounts(self, tmp_path):
        """Test that setting fallback order validates account names."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        # Set up with one account
        mgr.save_config({
            "password_hash": mgr.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {
                "work-claude": {"provider": "anthropic", "key": "xxx"},
            },
        })

        with pytest.raises(ValueError, match="Unknown account"):
            mgr.set_provider_fallback_order(["work-claude", "nonexistent"])

    def test_get_next_fallback_provider(self, tmp_path):
        """Test getting the next provider in fallback order."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.save_config({
            "password_hash": mgr.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {
                "first": {"provider": "anthropic", "key": "xxx"},
                "second": {"provider": "openai", "key": "xxx"},
                "third": {"provider": "gemini", "key": "xxx"},
            },
            "provider_fallback_order": ["first", "second", "third"],
        })

        assert mgr.get_next_fallback_provider("first") == "second"
        assert mgr.get_next_fallback_provider("second") == "third"
        assert mgr.get_next_fallback_provider("third") is None

    def test_get_next_fallback_provider_not_in_order(self, tmp_path):
        """Test getting next fallback when current is not in order."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.save_config({
            "password_hash": mgr.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {
                "first": {"provider": "anthropic", "key": "xxx"},
                "second": {"provider": "openai", "key": "xxx"},
                "other": {"provider": "gemini", "key": "xxx"},
            },
            "provider_fallback_order": ["first", "second"],
        })

        # When current account is not in order, return first in order
        assert mgr.get_next_fallback_provider("other") == "first"

    def test_get_next_fallback_provider_empty_order(self, tmp_path):
        """Test getting next fallback when order is empty."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        assert mgr.get_next_fallback_provider("anything") is None

    def test_fallback_order_persists(self, tmp_path):
        """Test that fallback order persists across instances."""
        config_path = tmp_path / "test.conf"

        mgr1 = ConfigManager(config_path)
        mgr1.save_config({
            "password_hash": mgr1.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {
                "a": {"provider": "anthropic", "key": "xxx"},
                "b": {"provider": "openai", "key": "xxx"},
            },
        })
        mgr1.set_provider_fallback_order(["a", "b"])

        mgr2 = ConfigManager(config_path)
        assert mgr2.get_provider_fallback_order() == ["a", "b"]


class TestUsageSwitchThreshold:
    """Test cases for usage switch threshold configuration."""

    def test_get_usage_threshold_default(self, tmp_path):
        """Test that default threshold is 90%."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        assert mgr.get_usage_switch_threshold() == 90

    def test_set_and_get_usage_threshold(self, tmp_path):
        """Test setting and getting the usage threshold."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.set_usage_switch_threshold(75)
        assert mgr.get_usage_switch_threshold() == 75

    def test_set_threshold_to_100_disables_usage_switching(self, tmp_path):
        """Test that 100% threshold effectively disables usage-based switching."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.set_usage_switch_threshold(100)
        assert mgr.get_usage_switch_threshold() == 100

    def test_set_threshold_validates_range(self, tmp_path):
        """Test that threshold must be between 0 and 100."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        with pytest.raises(ValueError, match="must be between 0 and 100"):
            mgr.set_usage_switch_threshold(-1)

        with pytest.raises(ValueError, match="must be between 0 and 100"):
            mgr.set_usage_switch_threshold(101)

    def test_threshold_persists(self, tmp_path):
        """Test that threshold persists across instances."""
        config_path = tmp_path / "test.conf"

        mgr1 = ConfigManager(config_path)
        mgr1.set_usage_switch_threshold(80)

        mgr2 = ConfigManager(config_path)
        assert mgr2.get_usage_switch_threshold() == 80


class TestMockRemainingUsage:
    """Test cases for mock remaining usage (for testing usage-based switching)."""

    def test_get_mock_usage_default(self, tmp_path):
        """Test that default mock usage is 0.5 (50%)."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        assert mgr.get_mock_remaining_usage("any-mock") == 0.5

    def test_set_and_get_mock_usage(self, tmp_path):
        """Test setting and getting mock remaining usage."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.set_mock_remaining_usage("mock-1", 0.3)
        assert mgr.get_mock_remaining_usage("mock-1") == 0.3

        mgr.set_mock_remaining_usage("mock-2", 0.8)
        assert mgr.get_mock_remaining_usage("mock-2") == 0.8

        # mock-1 should still be 0.3
        assert mgr.get_mock_remaining_usage("mock-1") == 0.3

    def test_mock_usage_validates_range(self, tmp_path):
        """Test that mock usage must be between 0.0 and 1.0."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            mgr.set_mock_remaining_usage("mock-1", -0.1)

        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            mgr.set_mock_remaining_usage("mock-1", 1.1)

    def test_mock_usage_persists(self, tmp_path):
        """Test that mock usage persists across instances."""
        config_path = tmp_path / "test.conf"

        mgr1 = ConfigManager(config_path)
        mgr1.set_mock_remaining_usage("test-mock", 0.25)

        mgr2 = ConfigManager(config_path)
        assert mgr2.get_mock_remaining_usage("test-mock") == 0.25


class TestContextSwitchThreshold:
    """Test cases for context switch threshold configuration."""

    def test_get_context_threshold_default(self, tmp_path):
        """Test that default threshold is 90%."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        assert mgr.get_context_switch_threshold() == 90

    def test_set_and_get_context_threshold(self, tmp_path):
        """Test setting and getting the context threshold."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.set_context_switch_threshold(75)
        assert mgr.get_context_switch_threshold() == 75

    def test_context_threshold_validates_range(self, tmp_path):
        """Test that context threshold must be between 0 and 100."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        with pytest.raises(ValueError, match="must be between 0 and 100"):
            mgr.set_context_switch_threshold(-1)

        with pytest.raises(ValueError, match="must be between 0 and 100"):
            mgr.set_context_switch_threshold(101)

    def test_context_threshold_persists(self, tmp_path):
        """Test that context threshold persists across instances."""
        config_path = tmp_path / "test.conf"

        mgr1 = ConfigManager(config_path)
        mgr1.set_context_switch_threshold(80)

        mgr2 = ConfigManager(config_path)
        assert mgr2.get_context_switch_threshold() == 80


class TestMockContextRemaining:
    """Test cases for mock context remaining (for testing context-based switching)."""

    def test_get_mock_context_default(self, tmp_path):
        """Test that default mock context is 1.0 (100%)."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        assert mgr.get_mock_context_remaining("any-mock") == 1.0

    def test_set_and_get_mock_context(self, tmp_path):
        """Test setting and getting mock context remaining."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        mgr.set_mock_context_remaining("mock-1", 0.3)
        assert mgr.get_mock_context_remaining("mock-1") == 0.3

        mgr.set_mock_context_remaining("mock-2", 0.8)
        assert mgr.get_mock_context_remaining("mock-2") == 0.8

        # mock-1 should still be 0.3
        assert mgr.get_mock_context_remaining("mock-1") == 0.3

    def test_mock_context_validates_range(self, tmp_path):
        """Test that mock context must be between 0.0 and 1.0."""
        config_path = tmp_path / "test.conf"
        mgr = ConfigManager(config_path)

        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            mgr.set_mock_context_remaining("mock-1", -0.1)

        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            mgr.set_mock_context_remaining("mock-1", 1.1)

    def test_mock_context_persists(self, tmp_path):
        """Test that mock context persists across instances."""
        config_path = tmp_path / "test.conf"

        mgr1 = ConfigManager(config_path)
        mgr1.set_mock_context_remaining("test-mock", 0.25)

        mgr2 = ConfigManager(config_path)
        assert mgr2.get_mock_context_remaining("test-mock") == 0.25


class TestConfigUIParity:
    """Test that both Gradio and CLI UIs expose all required config options.

    These tests enforce that any user-editable config key is exposed in BOTH UIs.
    If a new config option is added to CONFIG_BASE_KEYS that should be user-editable,
    it must be added to REQUIRED_UI_CONFIG_KEYS and exposed in both UIs.

    IMPORTANT: When adding a new config option:
    1. Add getter/setter to ConfigManager
    2. Add API endpoint in routes/config.py
    3. Add APIClient method in api_client.py
    4. Add UI element in web_ui.py (Gradio)
    5. Add menu option in cli/app.py (CLI)
    6. Add the key to REQUIRED_UI_CONFIG_KEYS below (or INTERNAL_KEYS if not user-editable)

    The test_all_config_keys_categorized test will FAIL if you add a new key
    to CONFIG_BASE_KEYS without categorizing it here. This is intentional -
    it forces you to decide whether the key needs UI exposure.
    """

    # Internal keys that are NOT user-editable via UI
    # These are system-managed or accessed via other UI paths (like account management)
    INTERNAL_KEYS = {
        "password_hash",      # Security - never exposed
        "encryption_salt",    # Security - never exposed
        "accounts",           # Managed via provider cards, not direct config
        "role_assignments",   # Managed via account role dropdowns
        "preferences",        # Container object, individual prefs exposed separately
        "projects",           # Per-project settings, not global config
        "mock_remaining_usage",    # Testing only - per-account mock via provider cards
        "mock_context_remaining",  # Testing only - per-account mock via provider cards
    }

    # Config keys that MUST be editable in both Gradio and CLI UIs
    REQUIRED_UI_CONFIG_KEYS = {
        "verification_agent",
        "preferred_verification_model",
        "cleanup_days",
        "provider_fallback_order",
        "usage_switch_threshold",
        "context_switch_threshold",
        "max_verification_attempts",
    }

    # Keys that are only in Gradio UI (makes sense for web-only settings)
    GRADIO_ONLY_KEYS = {"ui_mode"}

    def test_required_keys_subset_of_config_base_keys(self):
        """Ensure all required UI keys are valid CONFIG_BASE_KEYS."""
        all_required = self.REQUIRED_UI_CONFIG_KEYS | self.GRADIO_ONLY_KEYS
        invalid_keys = all_required - CONFIG_BASE_KEYS
        assert not invalid_keys, (
            f"Keys in REQUIRED_UI_CONFIG_KEYS not in CONFIG_BASE_KEYS: {invalid_keys}. "
            f"Either add them to CONFIG_BASE_KEYS or remove from REQUIRED_UI_CONFIG_KEYS."
        )

    def test_all_config_keys_categorized(self):
        """Ensure every CONFIG_BASE_KEY is categorized as internal, required, or gradio-only.

        This test FAILS if you add a new key to CONFIG_BASE_KEYS without deciding
        whether it needs UI exposure. This forces explicit categorization of all config keys.

        If you add a new config key:
        - Add to INTERNAL_KEYS if it's system-managed (not user-editable)
        - Add to REQUIRED_UI_CONFIG_KEYS if users should edit it in both UIs
        - Add to GRADIO_ONLY_KEYS if it only makes sense in the web UI
        """
        all_categorized = self.INTERNAL_KEYS | self.REQUIRED_UI_CONFIG_KEYS | self.GRADIO_ONLY_KEYS
        uncategorized = CONFIG_BASE_KEYS - all_categorized
        assert not uncategorized, (
            f"CONFIG_BASE_KEYS contains uncategorized keys: {uncategorized}. "
            f"Add each key to one of: INTERNAL_KEYS (system-managed), "
            f"REQUIRED_UI_CONFIG_KEYS (both UIs), or GRADIO_ONLY_KEYS (web UI only). "
            f"See TestConfigUIParity docstring for details."
        )

    # Mapping from config keys to patterns that indicate the key is exposed in UI
    # Some keys use different naming conventions in the UI code
    KEY_PATTERNS = {
        "verification_agent": ["verification_agent", "verification_pref"],
        "preferred_verification_model": ["verification_model", "preferred_verification_model"],
        "cleanup_days": ["cleanup_days", "retention_days", "cleanup_settings", "retention_input"],
        "provider_fallback_order": ["fallback_order"],
        "usage_switch_threshold": ["usage_threshold", "usage_switch"],
        "context_switch_threshold": ["context_threshold", "context_switch"],
        "max_verification_attempts": ["max_verification_attempts", "verification_attempts"],
        "ui_mode": ["ui_mode"],
    }

    def test_gradio_ui_exposes_all_required_keys(self):
        """Verify Gradio web_ui.py references all required config keys."""
        import pathlib
        import re

        web_ui_path = pathlib.Path(__file__).parent.parent / "src" / "chad" / "ui" / "gradio" / "web_ui.py"
        content = web_ui_path.read_text()

        all_gradio_keys = self.REQUIRED_UI_CONFIG_KEYS | self.GRADIO_ONLY_KEYS
        missing_keys = []

        for key in all_gradio_keys:
            # Get patterns for this key, or use the key itself as fallback
            patterns_to_check = self.KEY_PATTERNS.get(key, [key])

            # Check for any of the patterns in various forms
            found = False
            for pattern in patterns_to_check:
                search_patterns = [
                    rf'"{pattern}"',
                    rf"'{pattern}'",
                    rf"get_{pattern}",
                    rf"set_{pattern}",
                    rf"{pattern}_input",
                    rf"{pattern}_pref",
                    rf"on_{pattern}_change",
                    pattern,  # Direct reference
                ]
                if any(re.search(p, content, re.IGNORECASE) for p in search_patterns):
                    found = True
                    break

            if not found:
                missing_keys.append(key)

        assert not missing_keys, (
            f"Gradio web_ui.py is missing UI elements for config keys: {missing_keys}. "
            f"Add UI elements (input fields, sliders, etc.) and change handlers for these keys."
        )

    def test_cli_ui_exposes_all_required_keys(self):
        """Verify CLI app.py references all required config keys."""
        import pathlib
        import re

        cli_app_path = pathlib.Path(__file__).parent.parent / "src" / "chad" / "ui" / "cli" / "app.py"
        content = cli_app_path.read_text()

        missing_keys = []

        for key in self.REQUIRED_UI_CONFIG_KEYS:
            # Get patterns for this key, or use the key itself as fallback
            patterns_to_check = self.KEY_PATTERNS.get(key, [key])

            # Check for any of the patterns in various forms
            found = False
            for pattern in patterns_to_check:
                search_patterns = [
                    rf'"{pattern}"',
                    rf"'{pattern}'",
                    rf"get_{pattern}",
                    rf"set_{pattern}",
                    pattern,  # Direct reference
                ]
                if any(re.search(p, content, re.IGNORECASE) for p in search_patterns):
                    found = True
                    break

            if not found:
                missing_keys.append(key)

        assert not missing_keys, (
            f"CLI app.py is missing menu options for config keys: {missing_keys}. "
            f"Add settings menu options for these keys in run_settings_menu()."
        )
