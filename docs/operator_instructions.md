```markdown
# Operator Instructions

## Purpose
This document provides detailed guidance for the Operator AI (CUA model) on how to interact with and process the contents of this repository. The Operator AI should use these instructions to autonomously navigate the repository, execute tasks, and update statuses based on the defined workflows.

## Repository Navigation
- **Toolbox:**  
  - Reference `toolbox.json` for a curated list of web-based tools.
  - Use this file to retrieve tool URLs, descriptions, and key features when a task references a specific tool.

- **Tasks:**  
  - New task definitions are JSON files in the `/tasks` directory.
  - Each task file includes details such as the action to perform, target tool, parameters, priority, and a trigger field.

- **Actions:**  
  - Action scripts are stored in the `/actions` directory as YAML files.
  - Each action script defines a step-by-step workflow (e.g., open URL, click a button, type text) that the Operator AI will execute.

- **Configuration:**  
  - The `/config/trigger_config.json` file contains rules for triggering actions based on file changes in the `/tasks` directory.
  
- **Logs:**  
  - Execution outcomes, errors, and status updates are recorded in `/logs/execution_log.txt`.

## Task Processing Workflow
1. **Detection:**  
   - Monitor the `/tasks` folder for new or modified task files as specified by the trigger configuration.

2. **Interpretation:**  
   - Parse the task JSON to extract `action`, `toolName`, `parameters`, `priority`, and other relevant fields.
   - Look up the specified tool in `toolbox.json` to gather additional context if needed.

3. **Action Selection:**  
   - Match the task's `action` and `toolName` with the corresponding action script in `/actions` (e.g., `action_replit.yaml` for Replit tasks).

4. **Execution:**  
   - Follow the steps outlined in the action script, simulating human-like browser interactions (e.g., opening a URL, clicking, typing).

5. **Logging and Feedback:**  
   - Update the task's status and append a log entry in `/logs/execution_log.txt` with the outcome.
   - Flag any errors or failures for reprocessing or human review.

## Guidelines for the Operator AI
- **Modularity:**  
  Each task is self-contained and should reference a specific tool from the toolbox.
  
- **Consistency:**  
  Strictly follow the steps in the action scripts to ensure repeatable and reliable execution.
  
- **Feedback Loop:**  
  Log every action and update task statuses to enable continuous learning and system improvement.
```
