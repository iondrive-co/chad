"""Configuration management including password hashing, API key encryption, and app settings."""

import base64
import getpass
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import bcrypt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Base keys that may appear in the persisted config.
CONFIG_BASE_KEYS: set[str] = {
    "password_hash",
    "encryption_salt",
    "accounts",
    "role_assignments",
    "preferences",
    "verification_agent",
    "preferred_verification_model",
    "cleanup_days",
    "ui_mode",
    "projects",  # Per-project settings keyed by absolute project path
    "provider_fallback_order",  # List of account names for auto-switching on quota exhaustion
    "usage_switch_threshold",  # Percentage (0-100) of usage before auto-switching providers
    "mock_remaining_usage",  # Dict of account_name -> 0.0-1.0 for mock provider testing
    "context_switch_threshold",  # Percentage (0-100) of context usage before auto-switching providers
    "mock_context_remaining",  # Dict of account_name -> 0.0-1.0 for mock provider context testing
    "max_verification_attempts",  # Maximum verification attempts before giving up (default 5)
}


class ConfigManager:
    """Manages application configuration including accounts, preferences, and settings."""

    def __init__(self, config_path: Path | None = None):
        import os

        # Allow override via environment variable (for testing/screenshots)
        env_config = os.environ.get("CHAD_CONFIG")
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
            with open(self.config_path, "r", encoding="utf-8") as f:
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
            fd, tmp_path = tempfile.mkstemp(dir=self.config_path.parent, prefix=".chad_config_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
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
        return "password_hash" not in config

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

        config = {"password_hash": password_hash, "encryption_salt": encryption_salt, "accounts": {}, "ui_mode": "gradio"}
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
        password_hash = config.get("password_hash")

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

                config = {"password_hash": new_password_hash, "encryption_salt": encryption_salt, "accounts": {}, "ui_mode": "gradio"}
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
        reasoning: str | None = None,
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
        encryption_salt = base64.urlsafe_b64decode(config["encryption_salt"].encode())

        encrypted_key = self.encrypt_value(api_key, password, encryption_salt)

        if "accounts" not in config:
            config["accounts"] = {}

        config["accounts"][account_name] = {
            "provider": provider,
            "key": encrypted_key,
            "model": model or "default",
            "reasoning": reasoning or "default",
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
        if "accounts" in config and account_name in config["accounts"]:
            config["accounts"][account_name]["model"] = model
            self.save_config(config)

    def set_account_reasoning(self, account_name: str, reasoning: str) -> None:
        """Set reasoning effort for an account."""
        if not self.has_account(account_name):
            raise ValueError(f"Account '{account_name}' does not exist")

        config = self.load_config()
        if "accounts" in config and account_name in config["accounts"]:
            config["accounts"][account_name]["reasoning"] = reasoning
            self.save_config(config)

    def get_account_model(self, account_name: str) -> str:
        """Get the model configured for an account.

        Args:
            account_name: Account name to look up

        Returns:
            Model name, or 'default' if not configured
        """
        config = self.load_config()
        if "accounts" in config and account_name in config["accounts"]:
            return config["accounts"][account_name].get("model", "default")
        return "default"

    def get_account_reasoning(self, account_name: str) -> str:
        """Get the reasoning effort configured for an account."""
        config = self.load_config()
        if "accounts" in config and account_name in config["accounts"]:
            return config["accounts"][account_name].get("reasoning", "default")
        return "default"

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
        if "accounts" in config and account_name in config["accounts"]:
            account = config["accounts"][account_name]
            encryption_salt = base64.urlsafe_b64decode(config["encryption_salt"].encode())

            try:
                api_key = self.decrypt_value(account["key"], password, encryption_salt)
                return {"provider": account["provider"], "api_key": api_key}
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

        if "accounts" in config:
            for account_name, account_data in config["accounts"].items():
                accounts[account_name] = account_data["provider"]

        return accounts

    def has_account(self, account_name: str) -> bool:
        """Check if an account exists.

        Args:
            account_name: Account name to check

        Returns:
            True if account is stored
        """
        config = self.load_config()
        return "accounts" in config and account_name in config["accounts"]

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
        if "role_assignments" not in config:
            config["role_assignments"] = {}

        config["role_assignments"][role] = account_name
        self.save_config(config)

    def get_role_assignment(self, role: str) -> str | None:
        """Get the account assigned to a role.

        Args:
            role: Role name to look up

        Returns:
            Account name assigned to this role, or None if not assigned
        """
        if role != "CODING":
            return None
        config = self.load_config()
        return config.get("role_assignments", {}).get(role)

    def list_role_assignments(self) -> dict[str, str]:
        """List all role assignments.

        Returns:
            Dict mapping role names to account names
        """
        config = self.load_config()
        assignments = config.get("role_assignments", {}) or {}
        # Only the CODING role is supported in simple mode
        return {role: acct for role, acct in assignments.items() if role == "CODING"}

    def clear_role(self, role: str) -> None:
        """Remove a role assignment.

        Args:
            role: Role name to clear
        """
        config = self.load_config()
        if "role_assignments" in config and role in config["role_assignments"]:
            del config["role_assignments"][role]
            self.save_config(config)

    def delete_account(self, account_name: str) -> None:
        """Delete an account and any role assignments using it.

        Args:
            account_name: Account name to delete
        """
        config = self.load_config()

        # Remove from accounts
        if "accounts" in config and account_name in config["accounts"]:
            del config["accounts"][account_name]

        # Remove any role assignments using this account
        if "role_assignments" in config:
            roles_to_clear = [role for role, acct in config["role_assignments"].items() if acct == account_name]
            for role in roles_to_clear:
                del config["role_assignments"][role]

        self.save_config(config)

    def save_preferences(self, project_path: str) -> None:
        """Save user preferences for future sessions.

        Args:
            project_path: Default project path
        """
        config = self.load_config()
        config["preferences"] = {"project_path": project_path}
        self.save_config(config)

    def load_preferences(self) -> dict[str, str] | None:
        """Load saved user preferences.

        Returns:
            Dict with 'project_path' or None if not saved
        """
        config = self.load_config()
        return config.get("preferences")

    # Special marker value indicating verification is disabled
    VERIFICATION_NONE = "__verification_none__"

    def set_verification_agent(self, account_name: str | None) -> None:
        """Set the verification agent account.

        The verification agent defaults to the coding agent's provider until
        explicitly set. Once set, it persists even if the coding agent changes.

        Args:
            account_name: Account name to use for verification, None to reset to default,
                          or VERIFICATION_NONE ("__verification_none__") to disable verification
        """
        config = self.load_config()
        if account_name is None:
            # Remove the setting to revert to default behavior
            if "verification_agent" in config:
                del config["verification_agent"]
        elif account_name == self.VERIFICATION_NONE:
            # Store special marker to disable verification
            config["verification_agent"] = account_name
        else:
            if not self.has_account(account_name):
                raise ValueError(f"Account '{account_name}' does not exist")
            config["verification_agent"] = account_name
        self.save_config(config)

    def get_verification_agent(self) -> str | None:
        """Get the verification agent account.

        Returns:
            Account name for verification agent, VERIFICATION_NONE if verification is disabled,
            or None if not explicitly set (meaning it should default to the coding agent's provider)
        """
        config = self.load_config()
        account = config.get("verification_agent")
        # Return special marker value as-is
        if account == self.VERIFICATION_NONE:
            return account
        # Verify the account still exists
        if account and not self.has_account(account):
            return None
        return account

    def set_preferred_verification_model(self, model: str | None) -> None:
        """Set the preferred model for verification.

        This is stored separately from the verification agent's account model,
        allowing a different model to be used for verification than for coding.

        Args:
            model: Model name to use for verification, or None to clear
        """
        config = self.load_config()
        if model is None:
            if "preferred_verification_model" in config:
                del config["preferred_verification_model"]
        else:
            config["preferred_verification_model"] = model
        self.save_config(config)

    def get_preferred_verification_model(self) -> str | None:
        """Get the preferred model for verification.

        Returns:
            Model name for verification, or None if not explicitly set
        """
        config = self.load_config()
        return config.get("preferred_verification_model")

    def get_cleanup_days(self) -> int:
        """Get the number of days after which to clean up old files.

        Returns:
            Number of days (default 3)
        """
        config = self.load_config()
        return config.get("cleanup_days", 3)

    def set_cleanup_days(self, days: int) -> None:
        """Set the number of days after which to clean up old files.

        Args:
            days: Number of days (must be positive)
        """
        if days < 1:
            raise ValueError("cleanup_days must be at least 1")
        config = self.load_config()
        config["cleanup_days"] = days
        self.save_config(config)

    def get_ui_mode(self) -> str:
        """Get the UI mode preference.

        Returns:
            UI mode: "gradio" (default) or "cli"
        """
        config = self.load_config()
        return config.get("ui_mode", "gradio")

    def set_ui_mode(self, mode: str) -> None:
        """Set the UI mode preference.

        Args:
            mode: "gradio" or "cli"

        Raises:
            ValueError: If mode is not valid
        """
        if mode not in ("gradio", "cli"):
            raise ValueError(f"Invalid ui_mode: {mode}. Must be 'gradio' or 'cli'")
        config = self.load_config()
        config["ui_mode"] = mode
        self.save_config(config)

    def _normalize_project_path(self, project_path: str | Path) -> str:
        """Normalize a project path to an absolute path string for use as a key."""
        return str(Path(project_path).resolve())

    def get_project_config(self, project_path: str | Path) -> dict[str, Any] | None:
        """Get configuration for a specific project.

        Args:
            project_path: Path to the project root

        Returns:
            Project configuration dict, or None if not configured
        """
        config = self.load_config()
        projects = config.get("projects", {})
        key = self._normalize_project_path(project_path)
        return projects.get(key)

    def set_project_config(self, project_path: str | Path, project_config: dict[str, Any]) -> None:
        """Set configuration for a specific project.

        Args:
            project_path: Path to the project root
            project_config: Configuration dict to save
        """
        config = self.load_config()
        if "projects" not in config:
            config["projects"] = {}
        key = self._normalize_project_path(project_path)
        config["projects"][key] = project_config
        self.save_config(config)

    def delete_project_config(self, project_path: str | Path) -> None:
        """Delete configuration for a specific project.

        Args:
            project_path: Path to the project root
        """
        config = self.load_config()
        projects = config.get("projects", {})
        key = self._normalize_project_path(project_path)
        if key in projects:
            del projects[key]
            self.save_config(config)

    def list_project_configs(self) -> dict[str, dict[str, Any]]:
        """List all project configurations.

        Returns:
            Dict mapping project paths to their configurations
        """
        config = self.load_config()
        return config.get("projects", {})

    def get_provider_fallback_order(self) -> list[str]:
        """Get the ordered list of account names for auto-switching on quota exhaustion.

        When a provider runs out of credits/quota, the system will automatically
        switch to the next provider in this list.

        Returns:
            List of account names in fallback priority order
        """
        config = self.load_config()
        order = config.get("provider_fallback_order", [])
        # Filter out accounts that no longer exist
        valid_accounts = set(self.list_accounts().keys())
        return [acc for acc in order if acc in valid_accounts]

    def set_provider_fallback_order(self, account_names: list[str]) -> None:
        """Set the ordered list of account names for auto-switching.

        Args:
            account_names: List of account names in fallback priority order.
                          Accounts not in this list will not be used for auto-switching.

        Raises:
            ValueError: If any account name doesn't exist
        """
        valid_accounts = set(self.list_accounts().keys())
        invalid = [name for name in account_names if name not in valid_accounts]
        if invalid:
            raise ValueError(f"Unknown account(s): {', '.join(invalid)}")

        config = self.load_config()
        config["provider_fallback_order"] = account_names
        self.save_config(config)

    def get_next_fallback_provider(self, current_account: str) -> str | None:
        """Get the next provider in the fallback order after the current one.

        Args:
            current_account: The currently active account name

        Returns:
            Next account name in fallback order, or None if no more fallbacks
        """
        order = self.get_provider_fallback_order()
        if not order:
            return None

        try:
            current_idx = order.index(current_account)
            if current_idx + 1 < len(order):
                return order[current_idx + 1]
        except ValueError:
            # Current account not in fallback order, return first in order
            if order:
                return order[0]

        return None

    def get_usage_switch_threshold(self) -> int:
        """Get the usage percentage threshold for auto-switching providers.

        When a provider reports usage above this percentage of its limit,
        the system will automatically switch to the next fallback provider.

        Returns:
            Percentage threshold (0-100), defaults to 90
        """
        config = self.load_config()
        return config.get("usage_switch_threshold", 90)

    def set_usage_switch_threshold(self, percentage: int) -> None:
        """Set the usage percentage threshold for auto-switching providers.

        Args:
            percentage: Threshold percentage (0-100). Use 100 to disable
                       usage-based switching (only error-based switching).

        Raises:
            ValueError: If percentage is not between 0 and 100
        """
        if not 0 <= percentage <= 100:
            raise ValueError("usage_switch_threshold must be between 0 and 100")
        config = self.load_config()
        config["usage_switch_threshold"] = percentage
        self.save_config(config)

    def get_mock_remaining_usage(self, account_name: str) -> float:
        """Get mock remaining usage for a mock provider account.

        Used for testing usage-based provider switching without real providers.

        Args:
            account_name: The mock account name

        Returns:
            Remaining usage as 0.0-1.0 (1.0 = full capacity remaining)
        """
        config = self.load_config()
        usage_dict = config.get("mock_remaining_usage", {})
        return usage_dict.get(account_name, 0.5)  # Default to 50%

    def set_mock_remaining_usage(self, account_name: str, remaining: float) -> None:
        """Set mock remaining usage for a mock provider account.

        Args:
            account_name: The mock account name
            remaining: Remaining usage as 0.0-1.0 (1.0 = full capacity remaining)

        Raises:
            ValueError: If remaining is not between 0 and 1
        """
        if not 0.0 <= remaining <= 1.0:
            raise ValueError("mock_remaining_usage must be between 0.0 and 1.0")
        config = self.load_config()
        if "mock_remaining_usage" not in config:
            config["mock_remaining_usage"] = {}
        config["mock_remaining_usage"][account_name] = remaining
        self.save_config(config)

    def get_context_switch_threshold(self) -> int:
        """Get the context usage percentage threshold for auto-switching providers.

        When a provider's context window usage exceeds this percentage,
        the system will automatically switch to the next fallback provider.

        Returns:
            Percentage threshold (0-100), defaults to 90
        """
        config = self.load_config()
        return config.get("context_switch_threshold", 90)

    def set_context_switch_threshold(self, percentage: int) -> None:
        """Set the context usage percentage threshold for auto-switching providers.

        Args:
            percentage: Threshold percentage (0-100). Use 100 to disable
                       context-based switching (only error-based switching).

        Raises:
            ValueError: If percentage is not between 0 and 100
        """
        if not 0 <= percentage <= 100:
            raise ValueError("context_switch_threshold must be between 0 and 100")
        config = self.load_config()
        config["context_switch_threshold"] = percentage
        self.save_config(config)

    def get_mock_context_remaining(self, account_name: str) -> float:
        """Get mock context remaining for a mock provider account.

        Used for testing context-based provider switching without real providers.

        Args:
            account_name: The mock account name

        Returns:
            Remaining context as 0.0-1.0 (1.0 = full context available)
        """
        config = self.load_config()
        context_dict = config.get("mock_context_remaining", {})
        return context_dict.get(account_name, 1.0)  # Default to 100%

    def set_mock_context_remaining(self, account_name: str, remaining: float) -> None:
        """Set mock context remaining for a mock provider account.

        Args:
            account_name: The mock account name
            remaining: Remaining context as 0.0-1.0 (1.0 = full context available)

        Raises:
            ValueError: If remaining is not between 0 and 1
        """
        if not 0.0 <= remaining <= 1.0:
            raise ValueError("mock_context_remaining must be between 0.0 and 1.0")
        config = self.load_config()
        if "mock_context_remaining" not in config:
            config["mock_context_remaining"] = {}
        config["mock_context_remaining"][account_name] = remaining
        self.save_config(config)

    def get_max_verification_attempts(self) -> int:
        """Get the maximum number of verification attempts.

        Returns:
            Maximum attempts (default 5)
        """
        config = self.load_config()
        return config.get("max_verification_attempts", 5)

    def set_max_verification_attempts(self, attempts: int) -> None:
        """Set the maximum number of verification attempts.

        Args:
            attempts: Maximum attempts (1-20)

        Raises:
            ValueError: If attempts is not between 1 and 20
        """
        if not 1 <= attempts <= 20:
            raise ValueError("max_verification_attempts must be between 1 and 20")
        config = self.load_config()
        config["max_verification_attempts"] = attempts
        self.save_config(config)


def validate_config_keys(config: dict[str, Any], *, allow: Iterable[str] | None = None) -> None:
    """Ensure the config file only contains known keys.

    Raises:
        ValueError: If unknown keys are present.
    """
    if not isinstance(config, dict):
        raise ValueError("Config must be a dict")
    allowed_keys = set(CONFIG_BASE_KEYS)
    if allow:
        allowed_keys.update(allow)

    unknown = set(config.keys()) - allowed_keys
    if unknown:
        pretty = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown config keys found: {pretty}. Update the setup config panel to handle them.")
