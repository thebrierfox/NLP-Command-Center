# IntuiTek¹ Operator Repository

REPOSITORY SET UP INSTRUCTIONS ARE TO BE FOUND HERE: https://gist.github.com/thebrierfox/dc00a77897fe3090a2326ec6d76c7b6a

## Overview
This repository is the central command center for the Operator AI (CUA model). It is designed to be read and triggered by the Operator AI to autonomously execute tasks using a curated toolbox of web-based tools and predefined action workflows. Each file in this repository has been structured to enable clear communication, rapid task processing, and robust logging.

## Repository Structure
- **toolbox.json**: A curated list of web-based tools, including URLs, descriptions, and key features.
- **docs/operator_instructions.md**: Detailed instructions for the Operator AI on how to navigate, interpret, and act upon repository content.
- **tasks/**: Contains JSON files that define task triggers. New tasks are created here by copying the template file.
- **actions/**: YAML files that outline step-by-step workflows (action scripts) for specific tasks (e.g., launching a Replit session or initiating a Figma design session).
- **scripts/**: Browser-based utility scripts that the Operator AI can reference or execute.
- **config/**: Configuration files that define rules and metadata for automated triggers and task management.
- **logs/**: A directory where the Operator AI writes execution logs and status updates.

## How to Use
1. **Task Creation:**  
   Copy `tasks/new_task_template.json` to create a new task file (e.g., `tasks/task_001.json`) and update its fields as needed.

2. **Action Execution:**  
   The Operator AI monitors the `/tasks` folder, reads new or updated task files, and matches them to the corresponding action script in `/actions`.

3. **Tool Reference:**  
   Use `toolbox.json` to retrieve the URL, description, and features of the target tool referenced in a task.

4. **Logging and Feedback:**  
   All execution outcomes and errors are logged in `/logs/execution_log.txt`, ensuring a continuous feedback loop for learning and troubleshooting.

For complete details, review the operator instructions in `docs/operator_instructions.md`.
