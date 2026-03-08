"""Brave Search API plugin for Abraxas runtime."""
import json
import urllib.parse
import urllib.request
from typing import Optional
from core.tools import ToolPlugin


# Brave Search API endpoint
BRAVE_SEARCH_API = "https://api.search.brave.com/res/v1/web/search"


def _search(
    query: str,
    api_key: str,
    count: int = 10,
    offset: int = 0,
    time_range: Optional[str] = None,
    safesearch: str = "moderate",
    text_decorations: bool = False,
    spellcheck: bool = True,
) -> str:
    """
    Execute Brave Search API request.
    
    Args:
        query: Search query string
        api_key: Brave Search API key
        count: Number of results to return (1-20, default 10)
        offset: Pagination offset (default 0)
        time_range: Optional time filter (e.g., "pn" (past day), "pw" (past week), "pm" (past month))
        safesearch: Safe search level ("off", "moderate", "strict")
        text_decorations: Whether to include text decorations (default false)
        spellcheck: Enable spell checking (default true)
    
    Returns:
        JSON string with search results or error message
    """
    import gzip
    
    try:
        if not query.strip():
            return "plugin error: query cannot be empty"
        
        if not api_key:
            return "plugin error: BRAVE_API_KEY not configured"
        
        # Build query parameters
        params = {
            "q": query,
            "count": min(max(count, 1), 20),  # Clamp between 1-20
            "offset": max(offset, 0),
            "safesearch": safesearch if safesearch in ["off", "moderate", "strict"] else "moderate",
            "text_decorations": "true" if text_decorations else "false",
            "spellcheck": "true" if spellcheck else "false",
        }
        
        if time_range:
            params["freshness"] = time_range
        
        query_string = urllib.parse.urlencode(params)
        url = f"{BRAVE_SEARCH_API}?{query_string}"
        
        # Build request with proper headers
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        }
        
        req = urllib.request.Request(url, headers=headers)
        
        # Execute request
        with urllib.request.urlopen(req, timeout=10) as response:
            # Handle gzip compressed responses
            data_bytes = response.read()
            
            # Check if response is gzip compressed
            if response.info().get("Content-Encoding") == "gzip":
                data_bytes = gzip.decompress(data_bytes)
            
            data = json.loads(data_bytes.decode("utf-8"))
            
            # Extract and format results
            if "web" not in data:
                return json.dumps({
                    "query": query,
                    "total": 0,
                    "results": [],
                    "error": "No web results in response"
                }, indent=2, ensure_ascii=False)
            
            web_results = data["web"]
            results = web_results.get("results", [])
            
            # Format output
            formatted = {
                "query": query,
                "total": len(results),
                "results": []
            }
            
            for r in results:
                formatted["results"].append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("description", ""),
                })
            
            return json.dumps(formatted, indent=2, ensure_ascii=False)
    
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="ignore")
        return f"plugin error: HTTP {status} - {body[:200]}"
    
    except urllib.error.URLError as e:
        return f"plugin error: network error - {e.reason}"
    
    except json.JSONDecodeError as e:
        return f"plugin error: invalid JSON response - {e}"
    
    except Exception as exc:
        return f"plugin error: {exc}"


def _handle(payload: dict) -> str:
    """
    Plugin handler for Brave Search.
    
    Expected payload:
        query: str - search query (required)
        count: int - number of results (1-20, default 10)
        offset: int - pagination offset (default 0)
        time_range: str - time filter (e.g., "pn", "pw", "pm")
        safesearch: str - "off", "moderate", "strict" (default "moderate")
    
    Returns:
        JSON formatted search results or error message
    """
    try:
        import os
        
        # Get API key from environment
        api_key = os.environ.get("BRAVE_API_KEY", "")
        
        # Extract parameters
        query = str(payload.get("query", "")).strip()
        count = int(payload.get("count", 10))
        offset = int(payload.get("offset", 0))
        time_range = payload.get("time_range")
        safesearch = str(payload.get("safesearch", "moderate")).strip()
        
        return _search(
            query=query,
            api_key=api_key,
            count=count,
            offset=offset,
            time_range=time_range,
            safesearch=safesearch,
        )
    
    except Exception as exc:
        return f"plugin error: {exc}"


def register(registry) -> None:
    """Register the Brave Search tool plugin."""
    registry.register(
        ToolPlugin(
            name="brave_search",
            description="Search the web using Brave Search API.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results (1-20, default 10)",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Pagination offset (default 0)",
                        "minimum": 0,
                    },
                    "time_range": {
                        "type": "string",
                        "description": "Time filter: pn (past day), pw (past week), pm (past month)",
                        "enum": ["pn", "pw", "pm"],
                    },
                    "safesearch": {
                        "type": "string",
                        "description": "Safe search level",
                        "enum": ["off", "moderate", "strict"],
                    },
                },
                "required": ["query"],
            },
            handler=_handle,
        )
    )
