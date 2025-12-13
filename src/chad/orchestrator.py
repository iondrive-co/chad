"""Chad - A simple relay between CODING and MANAGEMENT AI sessions."""

from pathlib import Path

from .session_manager import SessionManager, get_coding_timeout, get_management_timeout
from .providers import ModelConfig


class Chad:
    """Chad relays messages between CODING and MANAGEMENT AI sessions."""

    def __init__(
        self,
        coding_config: ModelConfig,
        management_config: ModelConfig,
        project_path: Path,
        task_description: str,
        insane_mode: bool = False
    ):
        self.project_path = project_path
        self.task_description = task_description
        self.coding_config = coding_config
        self.management_config = management_config
        self.session_manager = SessionManager(coding_config, management_config, insane_mode)

    def run(self) -> bool:
        """Run Chad as a relay between the two AIs.

        Returns:
            True if task completed successfully
        """
        print("\n" + "=" * 70)
        print("CHAD - RELAY MODE")
        print("=" * 70)
        print(f"Project: {self.project_path}")
        print(f"Task: {self.task_description}")
        print("=" * 70 + "\n")

        if not self.session_manager.start_sessions(
            str(self.project_path),
            self.task_description
        ):
            print("Failed to start sessions")
            return False

        print("\n" + "=" * 70)
        print("SESSIONS ACTIVE - Beginning relay loop")
        print("MANAGEMENT AI will coordinate the CODING AI")
        print("=" * 70 + "\n")

        initial_prompt = f"""Task: {self.task_description}

Project: {self.project_path}

Begin implementation. Ask for any permissions or clarifications you need.
"""

        self.session_manager.send_to_coding(initial_prompt)

        max_retries = 3
        retry_count = 0
        coding_timeout = get_coding_timeout(self.coding_config.provider)
        management_timeout = get_management_timeout(self.management_config.provider)

        try:
            while self.session_manager.are_sessions_alive():
                coding_response = self.session_manager.get_coding_response(timeout=coding_timeout)

                if not coding_response:
                    retry_count += 1
                    if retry_count >= max_retries:
                        print(f"\nNo response from CODING AI after {max_retries} attempts")
                        print("This could be due to:")
                        print("  - Provider timeout or connection issue")
                        print("  - CODING AI session crashed")
                        print("  - Task too complex for single response")
                        break
                    else:
                        print(f"\nNo response from CODING AI (attempt {retry_count}/{max_retries})")
                        print("Retrying...")
                        continue

                # Reset retry counter on successful response
                retry_count = 0

                if self._is_task_complete_signal(coding_response):
                    print("\n" + "=" * 70)
                    print("TASK COMPLETE - MANAGEMENT AI confirmed")
                    print("=" * 70 + "\n")
                    return True

                relay_message = f"""CODING AI output:
---
{coding_response}
---

Analyze this output and provide the next instruction for the CODING AI.
"""

                self.session_manager.send_to_management(relay_message)

                management_response = self.session_manager.get_management_response(timeout=management_timeout)

                if not management_response:
                    print("No response from MANAGEMENT AI")
                    break

                if self._looks_like_no_more_action(management_response):
                    print("\n" + "=" * 70)
                    print("TASK COMPLETE - MANAGEMENT AI indicated no further action")
                    print("=" * 70 + "\n")
                    return True

                self.session_manager.send_to_coding(management_response)

        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
        finally:
            self.session_manager.stop_all()

        return False

    @staticmethod
    def _looks_like_no_more_action(response: str) -> bool:
        if not response:
            return False
        needle = "NO FURTHER ACTION NEEDED"
        return needle in response.upper()

    def _is_task_complete_signal(self, response: str) -> bool:
        completion_markers = [
            "TASK COMPLETE",
            "TASK_COMPLETE",
            "[COMPLETE]",
            "Implementation complete and verified"
        ]
        response_upper = response.upper()
        return any(marker.upper() in response_upper for marker in completion_markers)
