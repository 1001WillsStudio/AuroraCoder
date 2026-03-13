# ThinkWithTool

**ThinkWithTool** is an advanced AI agent framework designed for complex coding and research tasks. It leverages Native Tool Calling (OpenAI function calling format) with extended thinking/reasoning capabilities to provide a robust and precise interface for agentic operations.

## Core Philosophy

This project implements a **Code Agent** architecture, prioritizing powerful, direct tools over restricted environments. Unlike sandboxed code interpreters that only run snippets, ThinkWithTool provides:

*   **Persistent Terminal Access**: Stateful PowerShell sessions for running system commands, git operations, and environment management.
*   **Direct File Manipulation**: Full read/write capabilities on the codebase with intelligent code display.
*   **Native Tool Calling**: Structured, reliable OpenAI function calling format.
*   **Extended Thinking**: Supports models with reasoning/thinking capabilities (e.g., DeepSeek Reasoner).
*   **Session Isolation**: Each session gets its own cloned conda environment and working directory.

## Available Tools

The agent is equipped with a suite of tools defined in `src/tool_definitions.py`:

### 1. File Operations (`src/code_tools/file_operations.py`)
*   **`read_file`**: Reads file content and displays it in the code interpreter with line numbers and type checking.
*   **`write_file`**: Creates or completely overwrites files.
*   **`edit_file`**: Aider-style search and replace editing (see format below).
*   **`delete_file`**: Removes files from the filesystem.
*   **`close_file`**: Removes a file from the code interpreter view to reduce context usage.
*   **`list_directory`**: Explores the file system.
*   **`search_files`**: Searches for files by name pattern.

### 2. Development Tools
*   **`run_terminal_command`**: Executes shell commands in a persistent PowerShell session with conda environment activated. The shell's state (environment, working directory) is preserved between commands.
*   **`grep_search`**: Regex-based text search across the workspace (`src/code_tools/grep_search.py`).

### 3. Research Tools (`src/core_tools/`)
*   **`google_search`**: Web search functionality.
*   **`web_browser`**: Jina AI-powered web reader for extracting content from URLs.

### 4. Tool Store (`src/core_tools/tool_store_client.py`)
*   **`tool_store`**: A universal tool manager that allows searching for and executing thousands of public APIs and local utilities. Supports `search`, `execute`, and `info` actions.

### 5. Code Interpreter (`src/code_tools/code_interpreter.py`)
The code interpreter provides intelligent file display with:
*   Line-numbered code display
*   Pyright-based type checking for Python files (using the session's conda environment)
*   Multi-file consolidated view
*   Automatic error highlighting

## Edit File Format

The `edit_file` tool uses an **Aider-style search and replace** format:

**Parameters:**
- `target_file`: Path to the file to edit
- `start_line`: Line number to start searching from (1-based)
- `search_content`: Exact content to find and replace (whitespace and indentation matter)
- `replace_content`: The replacement content (use empty string to delete)

**Rules:**
- `search_content` must match file content exactly (indentation, newlines matter; trailing spaces are ignored)
- Include 1-3 lines of context to uniquely identify the location
- Make one edit per call; use multiple calls for multiple edits
- Use empty `replace_content` to delete content

**Example:**
To change a print statement on line 10:
```python
# Call edit_file with:
target_file: "main.py"
start_line: 10
search_content: 'print("hello")'
replace_content: 'print("world")'
```

## Session Management

ThinkWithTool provides isolated session environments:

*   **Isolated Conda Environments**: Each session clones a base conda environment, ensuring reproducibility and isolation.
*   **Configurable Base Environment**: Set `DEFAULT_BASE_ENV_NAME` in `src/config.py` to specify which conda environment to clone from.
*   **Persistent Shell**: Commands run in a persistent PowerShell session with the conda environment automatically activated.
*   **Automatic Cleanup**: Old sessions are automatically cleaned up (configurable limit).

## Project Structure

*   **`src/`**: Main application source.
    *   **`main_flow.py`**: Central logic for the chat loop, tool execution, and streaming responses.
    *   **`tool_definitions.py`**: Registry of all available tools and their OpenAI function schemas.
    *   **`config.py`**: Configuration for API endpoints, model settings, and session parameters.
    *   **`code_tools/`**: Implementation of coding-specific tools (File Ops, Terminal, Grep, Code Interpreter).
    *   **`core_tools/`**: General purpose tools (Search, Browser, Tool Store).
    *   **`code_sandbox/`**: Session manager for conda environment and working directory isolation.
*   **`code_archive/`**: Storage for deprecated or removed components.
*   **`data/`**: Storage for conversation logs.

## Getting Started

### Prerequisites
- Conda installed and available in PATH
- Python 3.11+
- Pyright for code analysis (optional but recommended)

### Installation

1.  **Clone the repository** and navigate to the project directory.

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure the API** (optional): Edit `src/config.py` to set your API endpoint and key.

### Running the Application

**Web API + React Frontend (single script):**
```powershell
.\start.ps1
```
This launches both the backend and frontend together. Press `Ctrl+C` to stop both.

Or start them manually in separate terminals:
```bash
# Terminal 1 — FastAPI backend
conda activate agent
python run_web.py
# Backend: http://localhost:8080
# API docs: http://localhost:8080/docs

# Terminal 2 — React frontend
cd frontend
npm install
npm run dev
# Frontend: http://localhost:3000
```

**Session CLI:**
```bash
conda activate agent
python -m src.code_sandbox.session_cli
```

Available subcommands: `create`, `list`, `cleanup`, `info`, `test`

## Configuration

Key settings in `src/config.py`:

| Setting | Description | Default |
|---------|-------------|---------|
| `BASE_URL` | OpenAI-compatible API endpoint | `https://api.deepseek.com/v1` |
| `MODEL_NAME` | Model to use | `deepseek-reasoner` |
| `MAX_ITERATIONS` | Max tool calls per turn | `30` |
| `CONTINUE_ITERATIONS` | Additional iterations on continue | `30` |
| `DEFAULT_BASE_ENV_NAME` | Base conda env to clone | `None` (uses `thinktool_sandbox_base`) |

## Requirements

Core dependencies (see `requirements.txt`):
- `openai>=1.0.0` - API client
- `fastapi>=0.104.1` - Web API
- `google-api-python-client>=2.169.0` - Google Search
- `pyright` - Python type checking (via nodejs)

## License

This project is provided as-is for research and development purposes.