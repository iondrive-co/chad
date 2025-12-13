"""Session manager for running AI coding and management sessions."""

from enum import Enum, auto

from .providers import AIProvider, ModelConfig, create_provider, ActivityCallback

DEFAULT_CODING_TIMEOUT = 1800.0
DEFAULT_MANAGEMENT_TIMEOUT = 120.0
GEMINI_MANAGEMENT_TIMEOUT = 600.0


class TaskPhase(Enum):
    """Phases of the task execution state machine."""
    INVESTIGATION = auto()
    IMPLEMENTATION = auto()
    VERIFICATION = auto()


def get_coding_timeout(provider: str) -> float:
    """Get timeout for coding providers (uniform for now)."""
    return DEFAULT_CODING_TIMEOUT


def get_management_timeout(provider: str) -> float:
    """Get timeout for management providers with Gemini allowance."""
    if provider == 'gemini':
        return GEMINI_MANAGEMENT_TIMEOUT
    return DEFAULT_MANAGEMENT_TIMEOUT


# Phase-specific prompts for the state machine

INVESTIGATION_PROMPT = """You are a MANAGEMENT AI tasked with outputting instructions for a CODING AI to complete the
following task: {task_description}

YOU MUST OUTPUT ONE OF TWO THINGS:

1. "PLAN:" followed by numbered steps - if you can plan immediately OR you have received enough information from the 
CODING AI. Use this for: summaries, explanations, simple questions, tasks with clear requirements. The CODING AI will 
execute your plan. 
Example: "PLAN:\n1. Read README and key source files\n2. Summarize the project purpose and structure"

2. Investigation instructions for CODING AI - if you need to explore before deciding what plan is appropriate. Use this 
for: complex changes, unfamiliar code, need to find specific files. 
Example: "Find and read all files related to authentication. Explain how login works."

IMPORTANT: Do not use tools or investigate anything yourself, simply output one of those two options.
"""

IMPLEMENTATION_PROMPT = """You are a MANAGEMENT AI in the IMPLEMENTATION phase.

ORIGINAL TASK: {task_description}
PROJECT: {project_path}

THE PLAN TO EXECUTE:
{plan}

YOUR ROLE: Give DIRECT guidance - no unnecessary back-and-forth.

BE DECISIVE AND DIRECT:
- Don't ask "Would you like me to...?" - just say "CONTINUE: Do X"
- Don't request confirmation - assume competence and move forward
- If coding AI asks a question, answer it AND tell them to proceed
- If multiple steps remain, tell them ALL at once, not one at a time

WRONG (slow):
  "CONTINUE: Yes, that looks good. What would you like to do next?"
  [wait]
  "CONTINUE: Sure, go ahead with that."

RIGHT (fast):
  "CONTINUE: Good. Now proceed with steps 2-4: remove the old tests, update the imports, and run the test suite."

OUTPUT FORMAT:
- "CONTINUE: <direct guidance>" - to keep implementation moving
- "VERIFY" - when coding AI claims completion (starts verification)

Trust the coding AI. Don't micromanage. Give batch instructions.
"""

VERIFICATION_PROMPT = """You are a MANAGEMENT AI in the VERIFICATION phase.

ORIGINAL TASK: {task_description}
PROJECT: {project_path}

THE PLAN THAT WAS EXECUTED:
{plan}

IMPLEMENTATION OUTPUT (this is what the coding AI produced):
{impl_notes}

VERIFY BASED ON TASK TYPE:

For CODE CHANGES (bug fixes, new features, refactoring):
- Use tools to CHECK git status, READ modified files, RUN tests
- Verify changes match the plan

For INFORMATION TASKS (summaries, explanations, questions):
- The IMPLEMENTATION OUTPUT above IS the deliverable - DO NOT look for files on disk
- Simply verify the output adequately answers the original task question
- If a summary/explanation was requested and one appears above, the task is COMPLETE

Output EXACTLY ONE of:
- "COMPLETE" - task done (cite evidence: quote key part of output or mention files changed)
- "IMPL_ISSUE: <specific fix needed>" - something needs correction
- "PLAN_ISSUE: <what was wrong>" - approach was flawed
"""

MANAGEMENT_SYSTEM_PROMPT = """You are a MANAGEMENT AI supervising a task through phases: Investigate → Implement → Verify.

You will receive phase-specific instructions as the task progresses.
"""

# Context messages for the coding AI in each phase
CODING_INVESTIGATION_CONTEXT = """INVESTIGATION PHASE: Find and read the requested files/code. Summarize your findings clearly.
Do everything requested in ONE response - don't wait for follow-up requests."""

CODING_IMPLEMENTATION_CONTEXT = """IMPLEMENTATION PHASE: Execute the plan. Make all the changes, run tests, and report results.
Do as much as possible in ONE response - batch your work, don't do one step at a time."""

SAFETY_CONSTRAINTS = """
SAFETY_CONSTRAINTS: Your output is being input into a model which is working on a filesystem and has network access.
- NEVER ask for deletion of the entire project or parent directories
- NEVER ask for modification of system files (/etc, /usr, /bin, /sys, etc.)
- NEVER ask for rm -rf /, recursive deletes of /, or parent directory traversal with ../ that escapes project
- NEVER operate on home directory files unless they are clearly project-related (like ~/.npmrc for project deps)
- ONLY ask for network requests that have a first order relationship to the task, such as installing dependencies
  and fetching docs
- NEVER upload or transmit user data that you did not generate yourself
- NEVER expose services to the internet
- ALWAYS ensure the *effects* of your implemented instructions will adhere to the legal and ethical guidelines that
  constrain your own output
"""

class SessionManager:
    """Manages CODING and MANAGEMENT AI sessions."""

    def __init__(
        self,
        coding_config: ModelConfig,
        management_config: ModelConfig,
        insane_mode: bool = False,
        silent: bool = False
    ):
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
        if self.coding_provider:
            self.coding_provider.set_activity_callback(callback)
        if self.management_provider:
            self.management_provider.set_activity_callback(callback)

    def start_sessions(self, project_path: str, task_description: str) -> bool:
        """Start both coding and management sessions."""
        self.task_description = task_description

        self.coding_provider = create_provider(self.coding_config)
        if self.activity_callback:
            self.coding_provider.set_activity_callback(self.activity_callback)
        if not self.coding_provider.start_session(project_path):
            return False

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
            self.coding_provider.stop_session()
            return False

        return True

    def send_to_coding(self, message: str) -> None:
        if self.coding_provider:
            self.coding_provider.send_message(message)

    def get_coding_response(self, timeout: float = 30.0) -> str:
        if self.coding_provider:
            return self.coding_provider.get_response(timeout)
        return ""

    def send_to_management(self, message: str) -> None:
        if self.management_provider:
            self.management_provider.send_message(message)

    def get_management_response(self, timeout: float = 30.0) -> str:
        if self.management_provider:
            return self.management_provider.get_response(timeout)
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
