"""Backend package for FastAPI services."""

import sys
import os
import importlib
from importlib.machinery import ModuleSpec

def get_mcp_src_dir():
    try:
        # Check relative to this file's directory:
        # backend/__init__.py -> video_summarizer -> workspace_root -> MCP_RAG_SERVER/src
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.abspath(os.path.join(base, "..", "..", "MCP_RAG_SERVER", "src"))
        if os.path.exists(path):
            return path
    except Exception:
        pass
        
    # Fallback to checking relative to current working directory
    try:
        candidates = [
            os.path.abspath(os.path.join(os.getcwd(), "..", "MCP_RAG_SERVER", "src")),
            os.path.abspath(os.path.join(os.getcwd(), "MCP_RAG_SERVER", "src")),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
    except Exception:
        pass
        
    return None

class AliasLoader:
    def __init__(self, target_module):
        self.target_module = target_module

    def create_module(self, spec):
        return self.target_module

    def exec_module(self, module):
        pass

class ModularRagRedirector:
    def __init__(self):
        self.mcp_src = get_mcp_src_dir()
        self.loading = set()

    def find_spec(self, fullname, path, target=None):
        if not self.mcp_src:
            return None
        
        # Prevent infinite recursion when importing redirected modules
        if fullname in self.loading:
            return None

        # 1. Provide virtual package spec for modular_rag
        if fullname == "modular_rag":
            spec = ModuleSpec(name=fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [self.mcp_src]
            return spec
            
        # 2. Redirect absolute imports inside modular_rag
        parts = fullname.split(".")
        top_level = parts[0]
        if top_level in ("libs", "ingestion", "observability", "mcp_server"):
            modular_name = "modular_rag." + fullname
            self.loading.add(modular_name)
            try:
                module = importlib.import_module(modular_name)
            finally:
                self.loading.remove(modular_name)
            spec = ModuleSpec(name=fullname, loader=AliasLoader(module))
            return spec
        elif top_level == "core":
            if len(parts) > 1 and parts[1] in ("settings", "types", "query_engine", "trace", "response"):
                modular_name = "modular_rag." + fullname
                self.loading.add(modular_name)
                try:
                    module = importlib.import_module(modular_name)
                finally:
                    self.loading.remove(modular_name)
                spec = ModuleSpec(name=fullname, loader=AliasLoader(module))
                return spec
        return None

# Register the redirector at the front of sys.meta_path
sys.meta_path.insert(0, ModularRagRedirector())
