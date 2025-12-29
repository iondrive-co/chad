"""Security functions for password hashing and API key encryption."""

import base64
import getpass
import json
import sys
from pathlib import Path
from typing import Any

import bcrypt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecurityManager:
    """Manages main password and API key encryption."""

    def __init__(self, config_path: Path | None = None):
        import os
        # Allow override via environment variable (for testing/screenshots)
        env_config = os.environ.get('CHAD_CONFIG')
        if env_config:
            self.config_path = Path(env_config)
        else:
            self.config_path = config_path or Path.home() / ".chad.conf"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _derive_encryption_key(self, password: str, salt: bytes) -> bytes:
        """Derive an encryption key from password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key

    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            Hashed password as string
        """
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode(), salt)
        return hashed.decode()

    def verify_password(self, password: str, hashed: str) -> bool:
        """Verify a password against its hash.

        Args:
            password: Plain text password to verify
            hashed: Stored password hash

        Returns:
            True if password matches
        """
        return bcrypt.checkpw(password.encode(), hashed.encode())

    def encrypt_value(self, value: str, password: str, salt: bytes) -> str:
        """Encrypt a value using the main password.

        Args:
            value: Plain text value to encrypt
            password: Main password
            salt: Salt for key derivation

        Returns:
            Encrypted value as base64 string
        """
        key = self._derive_encryption_key(password, salt)
        f = Fernet(key)
        encrypted = f.encrypt(value.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt_value(self, encrypted_value: str, password: str, salt: bytes) -> str:
        """Decrypt a value using the main password.

        Args:
            encrypted_value: Base64 encoded encrypted value
            password: Main password
            salt: Salt used for key derivation

        Returns:
            Decrypted plain text value

        Raises:
            Exception: If decryption fails (wrong password or corrupted data)
        """
        key = self._derive_encryption_key(password, salt)
        f = Fernet(key)
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_value.encode())
        decrypted = f.decrypt(encrypted_bytes)
        return decrypted.decode()

    def load_config(self) -> dict[str, Any]:
        """Load configuration from file.

        Returns:
            Configuration dictionary, or empty dict if file doesn't exist
        """
        if not self.config_path.exists():
            return {}

        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load config file: {e}")
            return {}

    def save_config(self, config: dict[str, Any]) -> None:
        """Save configuration to file atomically.

        Uses write-to-temp-then-rename pattern to avoid race conditions
        where concurrent reads could see a truncated/empty file.

        Args:
            config: Configuration dictionary to save
        """
        import os
        import tempfile

        try:
            # Write to temp file in same directory (for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=self.config_path.parent,
                prefix='.chad_config_',
                suffix='.tmp'
            )
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(config, f, indent=2)
                # Set permissions before rename
                os.chmod(tmp_path, 0o600)
                # Atomic rename
                os.replace(tmp_path, self.config_path)
            except Exception:
                # Clean up temp file on error
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except IOError as e:
            print(f"Error: Could not save config file: {e}")

    def is_first_run(self) -> bool:
        """Check if this is the first run (no config exists).

        Returns:
            True if config file doesn't exist or has no password hash
        """
        config = self.load_config()
        return 'password_hash' not in config

    def setup_main_password(self) -> str:
        """Prompt user to create a main password.

        Returns:
            The main password entered by the user
        """
        print("\n" + "=" * 70)
        print("MAIN PASSWORD SETUP")
        print("=" * 70)
        print("This password will be used to encrypt your API keys.")
        print("You will need to enter it every time you use Chad.")
        print("IMPORTANT: If you forget this password, your API keys will be lost!")
        print("=" * 70 + "\n")

        while True:
            sys.stdout.flush()
            password = getpass.getpass("Enter main password: ")

            # Warn about short or empty passwords before confirmation
            if len(password) == 0:
                print("\nWARNING: You are using an EMPTY password (no encryption).")
                print("Your API keys will NOT be encrypted!")
            elif len(password) < 8:
                print(f"\nWARNING: Your password is only {len(password)} character(s) long.")
                print("For better security, consider using a longer password (8+ characters).")

            sys.stdout.flush()
            confirm = getpass.getpass("Confirm main password: ")
            if password != confirm:
                print("Error: Passwords do not match. Please try again.")
                continue

            break

        # Hash the password and save to config
        password_hash = self.hash_password(password)

        # Generate a salt for encryption (different from bcrypt salt)
        encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

        config = {
            'password_hash': password_hash,
            'encryption_salt': encryption_salt,
            'accounts': {}
        }
        self.save_config(config)

        print("\nMain password configured successfully!")
        return password

    def verify_main_password(self) -> str:
        """Prompt user for main password and verify it.

        Returns:
            The verified main password

        Raises:
            SystemExit: If password verification fails and user declines reset
        """
        config = self.load_config()
        password_hash = config.get('password_hash')

        if not password_hash:
            raise ValueError("No main password configured")

        while True:
            sys.stdout.flush()
            password = getpass.getpass("\nEnter main password: ")

            if self.verify_password(password, password_hash):
                return password

            # Incorrect password - treat it as new password and ask to confirm reset
            print("\nIncorrect password.")
            print("\n" + "!" * 70)
            print("RESET MAIN PASSWORD")
            print("!" * 70)
            print("To reset with this password (this will DELETE all stored accounts),")
            print("reenter it below to confirm. Press Ctrl+D or Ctrl+C to cancel.")
            print("!" * 70)

            # Warn if password is short or empty
            if len(password) == 0:
                print("\nWARNING: You are using an EMPTY password (no encryption).")
                print("Your API keys will NOT be encrypted!")
            elif len(password) < 8:
                print(f"\nWARNING: Your password is only {len(password)} character(s) long.")
                print("For better security, consider using a longer password (8+ characters).")

            # Ask for confirmation
            try:
                sys.stdout.flush()
                confirm_password = getpass.getpass("\nReenter new main password to confirm: ")

                if password != confirm_password:
                    print("Error: Passwords do not match. Please try again.\n")
                    continue

                # Passwords match - create new config
                new_password_hash = self.hash_password(password)
                encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

                config = {
                    'password_hash': new_password_hash,
                    'encryption_salt': encryption_salt,
                    'accounts': {}
                }
                self.save_config(config)

                print("\nMain password reset complete. All stored accounts have been deleted.")
                return password
            except (EOFError, KeyboardInterrupt):
                # User cancelled reset - let them try password again
                print("\n\nReset cancelled. Please try again.")
                continue

    def store_account(
        self,
        account_name: str,
        provider: str,
        api_key: str,
        password: str,
        model: str | None = None,
        reasoning: str | None = None
    ) -> None:
        """Store a named account with encrypted API key.

        Args:
            account_name: Unique name for this account ('work-anthropic', 'personal-openai', etc.)
            provider: Provider name ('anthropic', 'openai', etc.)
            api_key: Plain text API key
            password: Main password for encryption
            model: Optional model name to use for this account
            reasoning: Optional reasoning effort to use for this account
        """
        config = self.load_config()
        encryption_salt = base64.urlsafe_b64decode(config['encryption_salt'].encode())

        encrypted_key = self.encrypt_value(api_key, password, encryption_salt)

        if 'accounts' not in config:
            config['accounts'] = {}

        config['accounts'][account_name] = {
            'provider': provider,
            'key': encrypted_key,
            'model': model or 'default',
            'reasoning': reasoning or 'default'
        }
        self.save_config(config)

    def set_account_model(self, account_name: str, model: str) -> None:
        """Set the model for an account.

        Args:
            account_name: Account name to update
            model: Model name to use
        """
        if not self.has_account(account_name):
            raise ValueError(f"Account '{account_name}' does not exist")

        config = self.load_config()
        if 'accounts' in config and account_name in config['accounts']:
            config['accounts'][account_name]['model'] = model
            self.save_config(config)

    def set_account_reasoning(self, account_name: str, reasoning: str) -> None:
        """Set reasoning effort for an account."""
        if not self.has_account(account_name):
            raise ValueError(f"Account '{account_name}' does not exist")

        config = self.load_config()
        if 'accounts' in config and account_name in config['accounts']:
            config['accounts'][account_name]['reasoning'] = reasoning
            self.save_config(config)

    def get_account_model(self, account_name: str) -> str:
        """Get the model configured for an account.

        Args:
            account_name: Account name to look up

        Returns:
            Model name, or 'default' if not configured
        """
        config = self.load_config()
        if 'accounts' in config and account_name in config['accounts']:
            return config['accounts'][account_name].get('model', 'default')
        return 'default'

    def get_account_reasoning(self, account_name: str) -> str:
        """Get the reasoning effort configured for an account."""
        config = self.load_config()
        if 'accounts' in config and account_name in config['accounts']:
            return config['accounts'][account_name].get('reasoning', 'default')
        return 'default'

    def get_account(self, account_name: str, password: str) -> dict[str, str] | None:
        """Retrieve account info including decrypted API key.

        Args:
            account_name: Account name to retrieve
            password: Main password for decryption

        Returns:
            Dict with 'provider' and 'api_key', or None if not found
        """
        config = self.load_config()

        # Check new format first
        if 'accounts' in config and account_name in config['accounts']:
            account = config['accounts'][account_name]
            encryption_salt = base64.urlsafe_b64decode(config['encryption_salt'].encode())

            try:
                api_key = self.decrypt_value(account['key'], password, encryption_salt)
                return {
                    'provider': account['provider'],
                    'api_key': api_key
                }
            except Exception as e:
                print(f"Error: Could not decrypt API key for {account_name}: {e}")
                return None

        # Fallback to old format for backward compatibility
        if 'api_keys' in config and account_name in config['api_keys']:
            encryption_salt = base64.urlsafe_b64decode(config['encryption_salt'].encode())
            try:
                api_key = self.decrypt_value(config['api_keys'][account_name], password, encryption_salt)
                return {
                    'provider': account_name,  # In old format, provider name was the key
                    'api_key': api_key
                }
            except Exception as e:
                print(f"Error: Could not decrypt API key for {account_name}: {e}")
                return None

        return None

    def list_accounts(self) -> dict[str, str]:
        """List all stored accounts with their providers.

        Returns:
            Dict mapping account names to provider names
        """
        config = self.load_config()
        accounts = {}

        # New format
        if 'accounts' in config:
            for account_name, account_data in config['accounts'].items():
                accounts[account_name] = account_data['provider']

        # Old format (for backward compatibility)
        if 'api_keys' in config:
            for provider_name in config['api_keys'].keys():
                if provider_name not in accounts:  # Don't override new format
                    accounts[provider_name] = provider_name

        return accounts

    def has_account(self, account_name: str) -> bool:
        """Check if an account exists.

        Args:
            account_name: Account name to check

        Returns:
            True if account is stored
        """
        config = self.load_config()

        if 'accounts' in config and account_name in config['accounts']:
            return True

        # Backward compatibility
        if 'api_keys' in config and account_name in config['api_keys']:
            return True

        return False

    # Legacy methods for backward compatibility
    def store_api_key(self, provider: str, api_key: str, password: str) -> None:
        """Legacy method - use store_account instead."""
        self.store_account(provider, provider, api_key, password)

    def get_api_key(self, provider: str, password: str) -> str | None:
        """Legacy method - use get_account instead."""
        account = self.get_account(provider, password)
        return account['api_key'] if account else None

    def has_api_key(self, provider: str) -> bool:
        """Legacy method - use has_account instead."""
        return self.has_account(provider)

    def list_stored_providers(self) -> list[str]:
        """Legacy method - use list_accounts instead."""
        return list(self.list_accounts().keys())

    def assign_role(self, account_name: str, role: str) -> None:
        """Assign a role to an account.

        Args:
            account_name: Account name to assign role to
            role: Role name ('CODING')

        Raises:
            ValueError: If account doesn't exist
        """
        if not self.has_account(account_name):
            raise ValueError(f"Account '{account_name}' does not exist")

        config = self.load_config()
        if 'role_assignments' not in config:
            config['role_assignments'] = {}

        config['role_assignments'][role] = account_name
        self.save_config(config)

    def get_role_assignment(self, role: str) -> str | None:
        """Get the account assigned to a role.

        Args:
            role: Role name to look up

        Returns:
            Account name assigned to this role, or None if not assigned
        """
        if role != 'CODING':
            return None
        config = self.load_config()
        return config.get('role_assignments', {}).get(role)

    def list_role_assignments(self) -> dict[str, str]:
        """List all role assignments.

        Returns:
            Dict mapping role names to account names
        """
        config = self.load_config()
        assignments = config.get('role_assignments', {}) or {}
        # Only the CODING role is supported in simple mode
        return {role: acct for role, acct in assignments.items() if role == 'CODING'}

    def clear_role(self, role: str) -> None:
        """Remove a role assignment.

        Args:
            role: Role name to clear
        """
        config = self.load_config()
        if 'role_assignments' in config and role in config['role_assignments']:
            del config['role_assignments'][role]
            self.save_config(config)

    def delete_account(self, account_name: str) -> None:
        """Delete an account and any role assignments using it.

        Args:
            account_name: Account name to delete
        """
        config = self.load_config()

        # Remove from accounts
        if 'accounts' in config and account_name in config['accounts']:
            del config['accounts'][account_name]

        # Remove any role assignments using this account
        if 'role_assignments' in config:
            roles_to_clear = [role for role, acct in config['role_assignments'].items() if acct == account_name]
            for role in roles_to_clear:
                del config['role_assignments'][role]

        self.save_config(config)

    def save_preferences(self, project_path: str) -> None:
        """Save user preferences for future sessions.

        Args:
            project_path: Default project path
        """
        config = self.load_config()
        config['preferences'] = {
            'project_path': project_path
        }
        self.save_config(config)

    def load_preferences(self) -> dict[str, str] | None:
        """Load saved user preferences.

        Returns:
            Dict with 'project_path' or None if not saved
        """
        config = self.load_config()
        return config.get('preferences')

    def set_verification_agent(self, account_name: str | None) -> None:
        """Set the verification agent account.

        The verification agent defaults to the coding agent's provider until
        explicitly set. Once set, it persists even if the coding agent changes.

        Args:
            account_name: Account name to use for verification, or None to reset to default
        """
        config = self.load_config()
        if account_name is None:
            # Remove the setting to revert to default behavior
            if 'verification_agent' in config:
                del config['verification_agent']
        else:
            if not self.has_account(account_name):
                raise ValueError(f"Account '{account_name}' does not exist")
            config['verification_agent'] = account_name
        self.save_config(config)

    def get_verification_agent(self) -> str | None:
        """Get the verification agent account.

        Returns:
            Account name for verification agent, or None if not explicitly set
            (meaning it should default to the coding agent's provider)
        """
        config = self.load_config()
        account = config.get('verification_agent')
        # Verify the account still exists
        if account and not self.has_account(account):
            return None
        return account
