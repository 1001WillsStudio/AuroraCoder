import datetime
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict
import uvicorn
import traceback
import json
from fastapi.responses import StreamingResponse

from .main_flow import generate_chat_responses_stream
# prepare_displayed_text was removed with the Gradio frontend

app = FastAPI()


class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]


class ChatResponse(BaseModel):
    response: str


@app.post("/chat/", response_model=ChatResponse)
def chat_handler(request: ChatRequest):
    """
    Handles a chat request by invoking the main chat generation logic.
    The request should contain a list of messages in OpenAI format.
    """
    last_text = ""
    # The generator now expects only the message list
    for completion_text, _, _ in generate_chat_responses_stream(
            messages=request.messages,
    ):
        last_text = completion_text

    # Safely split to prevent IndexError if </think> is not present
    response_parts = last_text.split("</think>", 1)
    cleaned_response = response_parts[1] if len(response_parts) > 1 else response_parts[0]
    return ChatResponse(response=cleaned_response)


@app.post("/chat_stream/", response_class=StreamingResponse)
async def chat_stream_handler(request: ChatRequest, process_html: bool = False):
    """
    Handles a chat request and streams the response.
    Expects messages in OpenAI format.
    """
    async def event_generator():
        try:
            # Call the updated stream generator
            for completion_text, _, _ in generate_chat_responses_stream(
                    messages=request.messages,
            ):
                payload = {}
                if process_html:
                    payload['type'] = 'processed_content'
                    payload['content'] = prepare_displayed_text(completion_text)
                else:
                    payload['type'] = 'raw_content'
                    payload['content'] = completion_text  # Raw text
                
                yield f"data: {json.dumps(payload)}\n\n"

        except Exception as e:
            print(f"Error during response generation stream: {traceback.format_exc()}")
            error_payload = {'type': 'error', 'source': 'stream_generator', 'message': str(e)}
            yield f"data: {json.dumps(error_payload)}\n\n"
        finally:
            yield f"event: close\ndata: Stream ended\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run("src.api:app", host="0.0.0.0", port=80, reload=False)
