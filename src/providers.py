"""
Model Provider Manager

Manages multiple OpenAI-compatible API clients for different model providers.
Allows runtime switching between providers (e.g., DeepSeek, NVIDIA, Vertex AI).

For Vertex AI providers, handles Google Cloud authentication with automatic token refresh.
"""

import os
from typing import Dict, List, Optional, Any
from openai import OpenAI

from .config import MODEL_PROVIDERS, DEFAULT_PROVIDER


# =============================================================================
# Vertex AI Client Wrapper
# =============================================================================

class VertexAIClient:
    """
    OpenAI-compatible client wrapper for Vertex AI.
    
    Handles Google Cloud authentication with automatic token refresh.
    Tokens expire after 1 hour, so we refresh before each API call.
    
    Requires either:
    - Application Default Credentials (run: gcloud auth application-default login)
    - GOOGLE_APPLICATION_CREDENTIALS env var pointing to a service account key
    """
    
    def __init__(self, project_id: str, location: str = "us-central1"):
        """
        Initialize the Vertex AI client.
        
        Args:
            project_id: Google Cloud project ID
            location: Vertex AI region (default: us-central1)
        """
        self.project_id = project_id
        self.location = location
        self._credentials = None
        self._client: Optional[OpenAI] = None
        # Vertex AI OpenAI-compatible endpoint
        # See: https://cloud.google.com/vertex-ai/generative-ai/docs/start/openai
        self._base_url = f"https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/endpoints/openapi"
        
        # Initialize credentials
        self._init_credentials()
    
    def _init_credentials(self):
        """Initialize Google Cloud credentials."""
        try:
            from google.auth import default
            import google.auth.transport.requests
            
            self._credentials, _ = default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._auth_request = google.auth.transport.requests.Request()
            print(f"[VertexAI] Credentials initialized for project: {self.project_id}")
        except Exception as e:
            print(f"[VertexAI] WARNING: Failed to initialize credentials: {e}")
            print("[VertexAI] Run 'gcloud auth application-default login' to authenticate")
            self._credentials = None
    
    def _refresh_token(self) -> str:
        """Refresh and return the access token."""
        if self._credentials is None:
            raise RuntimeError(
                "Google Cloud credentials not initialized. "
                "Run 'gcloud auth application-default login' or set GOOGLE_APPLICATION_CREDENTIALS"
            )
        
        # Refresh credentials if expired or about to expire
        if not self._credentials.valid:
            self._credentials.refresh(self._auth_request)
        
        return self._credentials.token
    
    def _get_client(self) -> OpenAI:
        """Get an OpenAI client with a fresh token."""
        token = self._refresh_token()
        
        # Create a new client with the refreshed token
        # We recreate to ensure the token is fresh
        self._client = OpenAI(
            base_url=self._base_url,
            api_key=token
        )
        return self._client
    
    @property
    def chat(self):
        """Provide access to chat.completions like the standard OpenAI client."""
        return _VertexAIChatNamespace(self)


class _VertexAIChatNamespace:
    """Namespace class to mimic OpenAI's client.chat structure."""
    
    def __init__(self, vertex_client: VertexAIClient):
        self._vertex_client = vertex_client
        self.completions = _VertexAICompletions(vertex_client)


class _VertexAICompletions:
    """Completions class that refreshes token before each call."""
    
    def __init__(self, vertex_client: VertexAIClient):
        self._vertex_client = vertex_client
    
    def create(self, **kwargs) -> Any:
        """
        Create a chat completion, refreshing the token first.
        
        This method is called for each API request and ensures
        the token is fresh before making the call.
        """
        client = self._vertex_client._get_client()
        return client.chat.completions.create(**kwargs)


# =============================================================================
# Provider Manager
# =============================================================================

class ProviderManager:
    """Manages multiple OpenAI-compatible API clients."""
    
    def __init__(self):
        self._clients: Dict[str, Any] = {}  # Can be OpenAI or VertexAIClient
        self._initialize_clients()
    
    def _initialize_clients(self):
        """Initialize clients for all configured providers."""
        for provider_id, config in MODEL_PROVIDERS.items():
            try:
                if config.get("provider_type") == "vertex_ai":
                    # Vertex AI provider - use special client with token refresh
                    self._init_vertex_client(provider_id, config)
                else:
                    # Standard OpenAI-compatible provider
                    api_key = config.get("api_key")
                    if not api_key or "YOUR_" in str(api_key):
                        # Skip if API key is missing or is a placeholder
                        # This prevents unconfigured providers from appearing in the list
                        if config.get("id") == "gemini-3-pro-api":
                            print(f"[ProviderManager] Skipping {provider_id}: API key not configured (GEMINI_API_KEY env var missing)")
                            continue
                            
                    self._clients[provider_id] = OpenAI(
                        base_url=config["base_url"],
                        api_key=api_key
                    )
                    print(f"[ProviderManager] Initialized: {provider_id} ({config['name']})")
            except Exception as e:
                print(f"[ProviderManager] Failed to initialize {provider_id}: {e}")
    
    def _init_vertex_client(self, provider_id: str, config: dict):
        """Initialize a Vertex AI client with Google Cloud auth."""
        # Get project ID from config or environment
        project_id = config.get("project_id") or os.environ.get("VERTEX_AI_PROJECT_ID")
        
        if not project_id:
            print(f"[ProviderManager] WARNING: {provider_id} requires VERTEX_AI_PROJECT_ID env var")
            print(f"[ProviderManager] Skipping {provider_id} - set VERTEX_AI_PROJECT_ID to enable")
            return
        
        location = config.get("location", "us-central1")
        
        try:
            client = VertexAIClient(project_id=project_id, location=location)
            self._clients[provider_id] = client
            print(f"[ProviderManager] Initialized Vertex AI: {provider_id} ({config['name']})")
        except Exception as e:
            print(f"[ProviderManager] Failed to initialize Vertex AI {provider_id}: {e}")
    
    def get_client(self, provider_id: str) -> OpenAI:
        """Get the client for a specific provider."""
        if provider_id not in self._clients:
            raise ValueError(f"Unknown provider: {provider_id}. Available: {list(self._clients.keys())}")
        return self._clients[provider_id]
    
    def get_config(self, provider_id: str) -> dict:
        """Get the configuration for a specific provider."""
        if provider_id not in MODEL_PROVIDERS:
            raise ValueError(f"Unknown provider: {provider_id}")
        return MODEL_PROVIDERS[provider_id]
    
    def list_providers(self) -> List[dict]:
        """List all available (initialized) providers with their info (for frontend)."""
        # Only return providers that were successfully initialized
        return [
            {
                "id": config["id"],
                "name": config["name"],
                "description": config["description"],
                "supports_thinking": config["supports_thinking"]
            }
            for provider_id, config in MODEL_PROVIDERS.items()
            if provider_id in self._clients
        ]
    
    def get_default_provider(self) -> str:
        """Get the default provider ID."""
        return DEFAULT_PROVIDER


# Global singleton instance
provider_manager = ProviderManager()


# Convenience functions for external use
def get_available_providers() -> List[dict]:
    """Get list of available model providers for the frontend."""
    return provider_manager.list_providers()


def get_default_provider() -> str:
    """Get the default provider ID."""
    return provider_manager.get_default_provider()
