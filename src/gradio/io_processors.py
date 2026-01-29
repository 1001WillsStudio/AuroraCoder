import datetime

from .csv_processor import sheet_to_markdown


def handle_file_upload(query_files: list, current_messages: list):
    """
    Handles file uploads by converting CSV files to markdown and appending them to the message list.
    """
    if query_files:
        # The query_files list contains file paths from Gradio's File component
        uploaded_file_path = query_files[0]
        print(f"Processing uploaded file for content: {uploaded_file_path}")
        csv_content = sheet_to_markdown(uploaded_file_path)
        current_messages.append({"role": "user", "content": f"## User Uploaded CSV\n{csv_content}\n\n"})
    return current_messages


def prepare_chat_messages(query: dict, history: list, messages: list):
    """
    Prepares the message list for the chat stream.
    System message initialization is handled by the main flow.
    """
    current_processing_messages = list(messages)

    if query.get("files"):
        current_processing_messages = handle_file_upload(query["files"], current_processing_messages)

    return current_processing_messages 