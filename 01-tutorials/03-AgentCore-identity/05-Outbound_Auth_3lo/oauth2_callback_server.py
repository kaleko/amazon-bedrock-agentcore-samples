"""
Sample OAuth2 Callback Server for Authorization Code flow ( 3LO ) with Amazon Bedrock AgentCore Identity

This module implements a local callback server that handles OAuth2 3-legged (3LO) authentication flows
for AgentCore Identity. It serves as an intermediary between the user's browser, external OAuth providers
(like Google, Github etc), and the AgentCore Identity service.

Key Components:
- FastAPI server running on localhost:9090
- Handles OAuth2 callback redirects from external providers
- Manages user token storage and session completion
- Provides health check endpoint for readiness verification

Usage Context:
This server is used in conjunction with agents running on AgentCore Runtime that need to access external resources
(like Google Calendar, Github repos) on behalf of authenticated users. The typical flow involves:
1. Agent requests access to external resource
2. User is redirected to OAuth provider for consent
3. Provider redirects back to this callback server
4. Server completes the authentication flow with AgentCore Identity
"""

import time
import uvicorn
import logging
import argparse
import requests

from datetime import timedelta
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from bedrock_agentcore.services.identity import IdentityClient, UserTokenIdentifier

# Configuration constants for the OAuth2 callback server
OAUTH2_CALLBACK_SERVER_PORT = 9090  # Port where the callback server listens
PING_ENDPOINT = "/ping"  # Health check endpoint
OAUTH2_CALLBACK_ENDPOINT = "/oauth2/callback"  # OAuth2 callback endpoint for provider redirects
USER_IDENTIFIER_ENDPOINT = "/userIdentifier/token"  # Endpoint to store user token identifiers

logger = logging.getLogger(__name__)


class OAuth2CallbackServer:
    """
    OAuth2 Callback Server for handling 3-legged OAuth flows with AgentCore Identity.
    
    This server acts as a local callback endpoint that external OAuth providers (like Google, Github)
    redirect to after user authorization. It manages the completion of the OAuth flow by
    coordinating with AgentCore Identity service.
    
    The server maintains:
    - An AgentCore Identity client for API communication
    - User token identifier for session binding
    - FastAPI application with configured routes
    """
    
    def __init__(self, region: str):
        """
        Initialize the OAuth2 callback server.
        
        Args:
            region (str): AWS region where AgentCore Identity service is deployed
        """
        # Initialize AgentCore Identity client for the specified region
        self.identity_client = IdentityClient(region=region)
        
        # Storage for user token identifier - used to bind OAuth sessions to specific users
        # This is set via the USER_IDENTIFIER_ENDPOINT before OAuth flow begins
        self.user_token_identifier = None
        
        # Create FastAPI application instance
        self.app = FastAPI()
        
        # Configure all HTTP routes
        self._setup_routes()

    def _setup_routes(self):
        """
        Configure FastAPI routes for the OAuth2 callback server.
        
        Sets up three endpoints:
        1. POST /userIdentifier/token - Store user token identifier for session binding
        2. GET /ping - Health check endpoint
        3. GET /oauth2/callback - OAuth2 callback handler for provider redirects
        """
        
        @self.app.post(USER_IDENTIFIER_ENDPOINT)
        async def _store_user_token(user_token_identifier_value: UserTokenIdentifier):
            """
            Store user token identifier for OAuth session binding.
            
            This endpoint is called before initiating the OAuth flow to associate
            the upcoming OAuth session with a specific user. The user token identifier
            is typically derived from the user's JWT token from inbound authentication.
            
            Args:
                user_token_identifier_value: UserTokenIdentifier object containing
                                           user identification information
            """
            self.user_token_identifier = user_token_identifier_value

        @self.app.get(PING_ENDPOINT)
        async def _handle_ping():
            """
            Health check endpoint to verify server readiness.
            
            Returns:
                dict: Simple status response indicating server is operational
            """
            return {"status": "success"}

        @self.app.get(OAUTH2_CALLBACK_ENDPOINT)
        async def _handle_oauth2_callback(session_id: str):
            """
            Handle OAuth2 callback from external providers.
            
            This is the core endpoint that external OAuth providers (like Google, Github) redirect to
            after user authorization. It receives the session_id parameter and uses it to
            complete the OAuth flow with AgentCore Identity.
            
            OAuth Flow Context:
            1. User clicks authorization URL generated by AgentCore Identity
            2. User authorizes access on external provider (e.g., Google, Github)
            3. Provider redirects to this callback with session_id
            4. This handler completes the flow by calling AgentCore Identity
            
            Args:
                session_id (str): Session identifier from OAuth provider redirect
                
            Returns:
                dict: Success message indicating OAuth flow completion
                
            Raises:
                HTTPException: If session_id is missing or user_token_identifier not set
            """
            # Validate that session_id parameter is present
            if not session_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing session_id query parameter",
                )

            # Ensure user token identifier was previously stored
            # This is required to bind the OAuth session to the correct user
            if not self.user_token_identifier:
                logger.error("No configured user token identifier")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Internal Server Error",
                )

            # Complete the OAuth flow by calling AgentCore Identity service
            # This associates the OAuth session with the user and retrieves access tokens
            self.identity_client.complete_resource_token_auth(
                session_uri=session_id, user_identifier=self.user_token_identifier
            )

            html_content = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>OAuth2 Success</title>
                <style>
                    body {
                        margin: 0;
                        padding: 0;
                        height: 100vh;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        font-family: Arial, sans-serif;
                        background-color: #f5f5f5;
                    }
                    .container {
                        text-align: center;
                        padding: 2rem;
                        background-color: white;
                        border-radius: 8px;
                        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
                    }
                    h1 {
                        color: #28a745;
                        margin: 0;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Completed OAuth2 3LO flow successfully</h1>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=200)

    def get_app(self) -> FastAPI:
        """
        Get the configured FastAPI application instance.
        
        Returns:
            FastAPI: The configured application with all routes set up
        """
        return self.app


def get_oauth2_callback_url() -> str:
    """
    Generate the full OAuth2 callback URL for external providers.
    
    This URL is registered with external OAuth providers (like Google, Github) as the redirect URI.
    After user authorization, the provider will redirect the user's browser to this URL
    with the session_id parameter.
    
    Returns:
        str: Complete callback URL (e.g., "http://localhost:9090/oauth2/callback")
    
    Usage:
        This URL is typically used when:
        1. Configuring OAuth2 credential providers in AgentCore Identity
        2. Registering redirect URIs with external OAuth providers
        3. Setting up workload identity allowed return URLs
    """
    return f"http://localhost:{OAUTH2_CALLBACK_SERVER_PORT}{OAUTH2_CALLBACK_ENDPOINT}"


def store_token_in_oauth2_callback_server(user_token_value: str):
    """
    Store user token identifier in the running OAuth2 callback server.
    
    This function sends a POST request to the callback server to store the user's
    token identifier before initiating the OAuth flow. The token identifier is
    used to bind the OAuth session to the specific user.
    
    Args:
        user_token_value (str): User token (typically JWT access token from Cognito)
                               used to identify the user in the OAuth flow
    
    Usage Context:
        Called before starting OAuth flow to ensure the callback server knows
        which user the OAuth session belongs to. This is critical for proper
        session binding in multi-user scenarios.
        
    Example:
        # Before invoking agent that requires OAuth
        bearer_token = reauthenticate_user(client_id)
        store_token_in_oauth2_callback_server(bearer_token)
    """
    if user_token_value:
        requests.post(
            f"http://localhost:{OAUTH2_CALLBACK_SERVER_PORT}{USER_IDENTIFIER_ENDPOINT}",
            json={"user_token": user_token_value},
            timeout=2,
        )
    else:
        logger.error("Ignoring: invalid user_token provided...")


def wait_for_oauth2_server_to_be_ready(
    duration: timedelta = timedelta(seconds=40),
) -> bool:
    """
    Wait for the OAuth2 callback server to become ready and responsive.
    
    This function polls the server's health check endpoint until it responds
    successfully or the timeout is reached. It's essential to ensure the server
    is ready before starting OAuth flows.
    
    Args:
        duration (timedelta): Maximum time to wait for server readiness
                             Defaults to 40 seconds
    
    Returns:
        bool: True if server becomes ready within timeout, False otherwise
    
    Usage Context:
        Called after starting the OAuth2 callback server process to ensure
        it's ready to handle OAuth callbacks before proceeding with agent
        invocations that might trigger OAuth flows.
        
    Example:
        # Start server process
        server_process = subprocess.Popen([...])
        
        # Wait for readiness
        if wait_for_oauth2_server_to_be_ready():
            # Proceed with OAuth-enabled operations
            invoke_agent()
        else:
            # Handle server startup failure
            server_process.terminate()
    """
    logger.info("Waiting for OAuth2 callback server to be ready...")
    timeout_in_seconds = duration.seconds

    start_time = time.time()
    while time.time() - start_time < timeout_in_seconds:
        try:
            # Ping the server's health check endpoint
            response = requests.get(
                f"http://localhost:{OAUTH2_CALLBACK_SERVER_PORT}{PING_ENDPOINT}",
                timeout=2,
            )
            if response.status_code == status.HTTP_200_OK:
                logger.info("OAuth2 callback server is ready!")
                return True
        except requests.exceptions.RequestException:
            # Server not ready yet, continue waiting
            pass

        time.sleep(2)
        elapsed = int(time.time() - start_time)
        
        # Log progress every 10 seconds to show we're still waiting
        if elapsed % 10 == 0 and elapsed > 0:
            logger.info(f"Still waiting... ({elapsed}/{timeout_in_seconds}s)")

    logger.error(
        f"Timeout: OAuth2 callback server not ready after {timeout_in_seconds} seconds"
    )
    return False


def main():
    """
    Main entry point for running the OAuth2 callback server as a standalone application.
    
    Parses command line arguments and starts the FastAPI server using uvicorn.
    The server runs on localhost:9090 and handles OAuth2 callbacks for the specified
    AWS region.
    
    Command Line Usage:
        python oauth2_callback_server.py --region us-east-1
        
    The server will run until manually terminated and will handle OAuth2 callbacks
    for any AgentCore agents in the specified region.
    """
    parser = argparse.ArgumentParser(description="OAuth2 Callback Server")
    parser.add_argument(
        "-r", "--region", type=str, required=True, help="AWS Region (e.g. us-east-1)"
    )

    args = parser.parse_args()
    oauth2_callback_server = OAuth2CallbackServer(region=args.region)

    # Start the FastAPI server using uvicorn
    # Server runs on localhost only for security (not exposed externally)
    uvicorn.run(
        oauth2_callback_server.get_app(),
        host="127.0.0.1",
        port=OAUTH2_CALLBACK_SERVER_PORT,
    )


if __name__ == "__main__":
    main()
