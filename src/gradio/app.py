import os
# Disable Gradio analytics to prevent PowerShell security warnings
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

import html
import traceback
from pathlib import Path
import logging

import gradio as gr
import gradio.themes as gr_themes

from .display_utils import format_chat_messages
from ..main_flow import generate_chat_responses_stream_native
from .io_processors import prepare_chat_messages
from ..code_sandbox import init_application_session
from ..config import CONTINUE_ITERATIONS, DEFAULT_BASE_ENV_NAME

logger = logging.getLogger(__name__)


def generate_gradio_responses_stream(
    query: dict, 
    history: list, 
    state_messages: list
):
    """
    Handles the Gradio chat interface using native tool calling system.
    
    Returns:
        Tuple of (formatted_messages, state_messages, max_iterations_reached_flag)
    """
    # Prepare messages using the I/O processor
    current_processing_messages = prepare_chat_messages(query, history, state_messages)

    user_query_text = query.get("text", "")
    if user_query_text:
        current_processing_messages.append({"role": "user", "content": user_query_text})

    # Call the native stream generator with the prepared messages
    completion_generator = generate_chat_responses_stream_native(
        messages=current_processing_messages,
    )

    final_status = "running"
    messages = current_processing_messages
    for response in completion_generator:
        messages = response["messages"]
        final_status = response["status"]
        # Return False for max_iterations_reached while processing
        yield format_chat_messages(messages), messages, False
    
    # Check if max iterations was reached
    if final_status == "max_iterations_reached":
        yield format_chat_messages(messages), messages, True


def convert_to_chatbot_tuples_format(state_messages: list) -> list:
    """
    Convert internal message format to Gradio chatbot tuples format.
    Gradio chatbot with tuples format expects: [[user_msg, bot_msg], ...]
    Uses the same display_utils formatting for consistency.
    """
    chatbot_tuples = []
    
    # Find all user message indices to split into turns
    user_indices = []
    for i, msg in enumerate(state_messages):
        if msg.get("role") == "user":
            user_indices.append(i)
    
    # Process each turn
    for turn_idx, user_idx in enumerate(user_indices):
        user_msg = state_messages[user_idx].get("content", "")
        
        # Get the end index for this turn (next user message or end of list)
        if turn_idx + 1 < len(user_indices):
            end_idx = user_indices[turn_idx + 1]
        else:
            end_idx = len(state_messages)
        
        # Get messages for this turn (from user to before next user)
        turn_messages = state_messages[user_idx:end_idx]
        
        # Use the same format_chat_messages for consistent styling
        bot_response = format_chat_messages(turn_messages)
        
        chatbot_tuples.append([user_msg, bot_response])
    
    return chatbot_tuples


def continue_generation(history: list, state_messages: list):
    """
    Continues generation from where it left off without adding a user message.
    Extends the iteration limit by CONTINUE_ITERATIONS.
    """
    if not state_messages:
        yield history, state_messages, False, gr.update(visible=False)
        return
    
    # Hide button immediately when clicked
    yield history, state_messages, False, gr.update(visible=False)
    
    # Continue from the current state without adding a user message
    completion_generator = generate_chat_responses_stream_native(
        messages=state_messages,
        max_iterations=CONTINUE_ITERATIONS,
    )

    final_status = "running"
    messages = state_messages
    for response in completion_generator:
        messages = response["messages"]
        final_status = response["status"]
        chatbot_format = convert_to_chatbot_tuples_format(messages)
        yield chatbot_format, messages, False, gr.update(visible=False)
    
    # Check if max iterations was reached again
    if final_status == "max_iterations_reached":
        chatbot_format = convert_to_chatbot_tuples_format(messages)
        yield chatbot_format, messages, True, gr.update(visible=True)


def escape_visible(text: str) -> str:
    """Escapes HTML characters in text for safe display."""
    return html.escape(text)


if __name__ == '__main__':
    # Initialize session before starting the app
    logger.info("Initializing session environment...")
    try:
        session_info = init_application_session(
            app_name="gradio_assistant",
            cleanup_on_exit=True,
            max_old_sessions=10,
            base_env_name=DEFAULT_BASE_ENV_NAME  # Use configured base environment
        )
        
        if session_info['status'] == 'failed':
            logger.error(f"Session creation failed: {session_info.get('error', 'Unknown error')}")
            print("⚠️  Session creation failed. Running in fallback mode.")
        else:
            print(f"✅ Session initialized successfully!")
            print(f"📁 Working directory: {session_info['session_dir']}")
            print(f"🐍 Conda environment: {session_info['conda_env_name']}")
            print(f"📦 Cloned from: {session_info.get('base_env_name', 'default')}")
            
    except Exception as e:
        logger.error(f"Failed to initialize session: {e}")
        print(f"⚠️  Session initialization failed: {e}")
        print("Running in fallback mode.")
    
        initial_messages = []

    custom_css = """
    .message.user { 
        background-color: #DCF8C6 !important; 
        align-self: flex-end; 
        border-radius: 10px 10px 0 10px !important; 
        margin-right: 5px !important; 
    }
    .message.bot { 
        background-color: #F0F0F0 !important; 
        align-self: flex-start; 
        border-radius: 10px 10px 10px 0 !important; 
        margin-left: 5px !important; 
    }
    .pending.message.user { 
        background-color: #DCF8C6 !important; 
        align-self: flex-end; 
        border-radius: 10px 10px 0 10px !important; 
        margin-right: 5px !important; 
    }
    .pending.message.bot { 
        background-color: #F0F0F0 !important; 
        align-self: flex-start; 
        border-radius: 10px 10px 10px 0 !important; 
        margin-left: 5px !important; 
    }
    """

    continue_btn_css = """
    .continue-btn {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
        border: none !important;
        color: white !important;
        font-weight: 600 !important;
        padding: 12px 24px !important;
        border-radius: 8px !important;
        cursor: pointer !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4) !important;
    }
    .continue-btn:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6) !important;
    }
    .continue-container {
        display: flex;
        justify-content: center;
        padding: 10px 0;
    }
    """

    with gr.Blocks(fill_height=True, theme=gr_themes.Soft(), css=custom_css + continue_btn_css) as demo:
        initial_messages = []
        state_messages = gr.State(initial_messages)
        max_iterations_reached = gr.State(False)

        chat_interface = gr.ChatInterface(
            fn=generate_gradio_responses_stream,
            multimodal=True,
            textbox=gr.MultimodalTextbox(
                placeholder="Type your message or click 📎 to attach a file",
                file_types=[".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".py", ".js", ".md", ".json"],
                file_count="single",
                sources=["upload"],
                label=None,
                max_plain_text_length=1000000,
            ),
            additional_inputs=[state_messages],
            additional_outputs=[state_messages, max_iterations_reached],
            fill_height=True,
            title="AI Assistant - ThinkWithTool",
            description="Advanced AI Assistant with Native Tool Calling and Thinking Capabilities",
        )
        
        # Add Continue button below the chat interface
        with gr.Row(elem_classes="continue-container"):
            continue_btn = gr.Button(
                "🔄 Continue (Max iterations reached - click to extend)",
                visible=False,
                elem_classes="continue-btn",
                variant="primary",
                size="lg"
            )
        
        # Update button visibility when max_iterations_reached state changes
        max_iterations_reached.change(
            fn=lambda reached: gr.update(visible=reached),
            inputs=[max_iterations_reached],
            outputs=[continue_btn],
        )
        
        # Wire up the Continue button
        continue_btn.click(
            fn=continue_generation,
            inputs=[chat_interface.chatbot, state_messages],
            outputs=[chat_interface.chatbot, state_messages, max_iterations_reached, continue_btn],
        )

    SCRIPT_DIR = Path(__file__).resolve().parent.parent.name
    print(SCRIPT_DIR)
    
    # Session cleanup is handled automatically by init_application_session
    demo.queue(default_concurrency_limit=10).launch(
        share=False,
        show_api=False
    )
