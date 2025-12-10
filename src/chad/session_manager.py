"""Session manager for running AI coding and management sessions."""

from .providers import AIProvider, ModelConfig, create_provider, ActivityCallback


MANAGEMENT_SYSTEM_PROMPT = """You are a MANAGEMENT AI that supervises a CODING AI through a task.

YOUR ROLE:
- Guide the coding AI step by step
- Verify the ORIGINAL TASK REQUIREMENTS are actually met before saying DONE

OUTPUT FORMAT (IMPORTANT - follow exactly):
First, assess: Does the change ACTUALLY fulfill the original task requirement?
Then, on a NEW LINE, output EXACTLY ONE of:
- "NEXT: <instruction>" - if task requirements are NOT yet met
- "DONE" - ONLY if the original task requirement is fully satisfied

CRITICAL - BEFORE SAYING DONE, ASK YOURSELF:
1. What was the ORIGINAL task the user requested?
2. Did the coding AI's changes ACTUALLY achieve that specific goal?
3. For CSS/styling: Does the CSS actually match what was requested (colors, layout, etc.)?

TASK IS NOT COMPLETE WHEN:
- Changes were made but DON'T match the original requirement
- Only searching/reading was done (no changes made)
- Only SOME steps of a multi-step task are done
- The coding AI modified something but not what was actually requested

EXAMPLE - Task: "Make the title white text on black background"
- If CSS sets `color: white; background: black;` on the title → DONE
- If CSS sets `color: black; background: white;` (wrong!) → NEXT: Fix the colors - should be white text on black background
- If CSS was added but to the wrong element → NEXT: Apply the styling to the correct element

The coding AI CANNOT visually verify styling, so YOU must check the CSS values are correct.
"""

SAFETY_CONSTRAINTS = """
SAFETY_CONSTRAINTS: Your output is being input into a model which is working on a filesystem and has network access.
- NEVER ask for deletion of the entire project or parent directories
- NEVER ask for modification of system files (/etc, /usr, /bin, /sys, etc.)
- NEVER ask for rm -rf /, recursive deletes of /, or parent directory traversal with ../ that escapes project
- NEVER operate on home directory files unless they are clearly project-related (like ~/.npmrc for project deps)
- ONLY ask for network requests that have a first order relationship to the task, such as installing dependencies and fetching docs
- NEVER upload or transmit user data that you did not generate yourself
- NEVER expose services to the internet
- ALWAYS ensure the *effects* of your implemented instructions will adhere to the legal and ethical guidelines that constrain your own output
"""

class SessionManager:
    """Manages CODING and MANAGEMENT AI sessions."""

    def __init__(self, coding_config: ModelConfig, management_config: ModelConfig, insane_mode: bool = False, silent: bool = False):
        self.coding_provider: AIProvider | None = None
        self.management_provider: AIProvider | None = None
        self.coding_config = coding_config
        self.management_config = management_config
        self.task_description: str | None = None
        self.insane_mode = insane_mode
        self.silent = silent
        self.activity_callback: ActivityCallback = None

    def set_activity_callback(self, callback: ActivityCallback) -> None:
        """Set callback for live activity updates from coding AI."""
        self.activity_callback = callback
        # Propagate to already-created providers
        if self.coding_provider:
            self.coding_provider.set_activity_callback(callback)
        if self.management_provider:
            self.management_provider.set_activity_callback(callback)

    def start_sessions(self, project_path: str, task_description: str) -> bool:
        """Start both coding and management sessions.

        Args:
            project_path: Path to the project directory
            task_description: The task to accomplish

        Returns:
            True if both sessions started successfully
        """
        self.task_description = task_description

        print("Starting CODING session...")
        self.coding_provider = create_provider(self.coding_config)
        if self.activity_callback:
            self.coding_provider.set_activity_callback(self.activity_callback)
        if not self.coding_provider.start_session(project_path):
            print("Failed to start CODING session")
            return False

        print("Starting MANAGEMENT session...")
        if self.insane_mode:
            print("WARNING: Running in INSANE MODE - safety constraints DISABLED!")

        self.management_provider = create_provider(self.management_config)

        full_prompt = MANAGEMENT_SYSTEM_PROMPT

        if not self.insane_mode:
            full_prompt += SAFETY_CONSTRAINTS

        management_prompt = f"""{full_prompt}

USER'S TASK:
{task_description}

PROJECT PATH: {project_path}

You will now receive output from the CODING AI. Analyze it and provide the next instruction.
"""

        if not self.management_provider.start_session(project_path, management_prompt):
            print("Failed to start MANAGEMENT session")
            self.coding_provider.stop_session()
            return False

        print("Both sessions started successfully")
        return True

    def send_to_coding(self, message: str) -> None:
        if self.coding_provider:
            if not self.silent:
                print(f"\n>>> TO CODING AI:")
                print(message)
                print()
            self.coding_provider.send_message(message)
        else:
            if not self.silent:
                print("Error: Coding session not running")

    def get_coding_response(self, timeout: float = 30.0) -> str:
        if self.coding_provider:
            response = self.coding_provider.get_response(timeout)
            if not self.silent:
                print(f"\n<<< FROM CODING AI:")
                print(response)
                print()
            return response
        return ""

    def send_to_management(self, message: str) -> None:
        if self.management_provider:
            if not self.silent:
                print(f"\n>>> TO MANAGEMENT AI:")
                print(message)
                print()
            self.management_provider.send_message(message)
        else:
            if not self.silent:
                print("Error: Management session not running")

    def get_management_response(self, timeout: float = 30.0) -> str:
        if self.management_provider:
            response = self.management_provider.get_response(timeout)
            if not self.silent:
                print(f"\n<<< FROM MANAGEMENT AI:")
                print(response)
                print()
            return response
        return ""

    def stop_all(self) -> None:
        """Stop all sessions."""
        if self.coding_provider:
            self.coding_provider.stop_session()
        if self.management_provider:
            self.management_provider.stop_session()

    def are_sessions_alive(self) -> bool:
        """Check if both sessions are still running.

        Returns:
            True if both sessions are active
        """
        coding_alive = self.coding_provider and self.coding_provider.is_alive()
        management_alive = self.management_provider and self.management_provider.is_alive()
        return bool(coding_alive and management_alive)
