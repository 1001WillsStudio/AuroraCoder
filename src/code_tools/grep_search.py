import re
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import fnmatch
from ..code_sandbox import session_manager  # Local import


class GrepSearch:
    """Fast text-based regex search tool using ripgrep-like functionality."""
    
    def __init__(self, workspace_root: str = None):
        if workspace_root:
            self.workspace_root = Path(workspace_root)
        elif session_manager and session_manager.session_dir:
            self.workspace_root = session_manager.get_session_working_directory()
        else:
            self.workspace_root = Path.cwd()
    
    def search(self, query: str, include_pattern: str = None, exclude_pattern: str = None, 
               case_sensitive: bool = True, max_results: int = 50) -> str:
        """
        Search for exact text matches or regex patterns within files.
        
        Args:
            query: The regex pattern to search for
            include_pattern: Glob pattern for files to include (e.g. '*.py')
            exclude_pattern: Glob pattern for files to exclude
            case_sensitive: Whether the search should be case sensitive
            max_results: Maximum number of matches to return
            
        Returns:
            Search results formatted as a string
        """
        try:
            # Compile regex pattern
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(query, flags)
            except re.error as e:
                return f"Error: Invalid regex pattern '{query}': {str(e)}"
            
            # Get files to search
            files_to_search = self._get_files_to_search(include_pattern, exclude_pattern)
            
            # Search through files
            matches = []
            for file_path in files_to_search:
                file_matches = self._search_file(file_path, pattern)
                matches.extend(file_matches)
                
                if len(matches) >= max_results:
                    break
            
            # Format results
            return self._format_results(query, matches[:max_results], len(matches) > max_results)
            
        except Exception as e:
            return f"Error during search: {str(e)}"
    
    def _get_files_to_search(self, include_pattern: str = None, exclude_pattern: str = None) -> List[Path]:
        """Get list of files to search based on include/exclude patterns."""
        files = []
        
        # Default to common text file patterns if no include pattern specified
        if include_pattern is None:
            include_patterns = ['*.py', '*.js', '*.ts', '*.tsx', '*.jsx', '*.java', '*.cpp', '*.c', '*.h',
                               '*.cs', '*.php', '*.rb', '*.go', '*.rs', '*.swift', '*.kt', '*.scala',
                               '*.clj', '*.sh', '*.bash', '*.zsh', '*.ps1', '*.sql', '*.html', '*.css',
                               '*.scss', '*.sass', '*.less', '*.xml', '*.json', '*.yaml', '*.yml',
                               '*.toml', '*.ini', '*.cfg', '*.conf', '*.md', '*.rst', '*.txt']
        else:
            include_patterns = [include_pattern]
        
        # Walk through workspace
        for file_path in self.workspace_root.rglob('*'):
            if not file_path.is_file():
                continue
            
            # Skip hidden files and common ignore patterns
            if self._should_ignore_file(file_path):
                continue
            
            # Check include patterns
            include_match = False
            for pattern in include_patterns:
                if fnmatch.fnmatch(file_path.name, pattern):
                    include_match = True
                    break
            
            if not include_match:
                continue
            
            # Check exclude pattern
            if exclude_pattern and fnmatch.fnmatch(file_path.name, exclude_pattern):
                continue
            
            files.append(file_path)
        
        return files
    
    def _should_ignore_file(self, file_path: Path) -> bool:
        """Check if file should be ignored based on common patterns."""
        ignore_patterns = [
            '.git', 'node_modules', '__pycache__', '.pyc', '.pyo', '.pyd',
            '.so', '.dylib', '.dll', '.exe', '.bin', '.log', '.tmp', '.temp',
            '.cache', '.venv', 'venv', 'env', '.env', 'dist', 'build', 'target',
            '.idea', '.vscode', '.vs'
        ]
        
        # Check if any part of the path matches ignore patterns
        parts = file_path.parts
        for part in parts:
            if any(ignore_pattern in part for ignore_pattern in ignore_patterns):
                return True
        
        return False
    
    def _search_file(self, file_path: Path, pattern: re.Pattern) -> List[Dict[str, Any]]:
        """Search for pattern matches in a single file."""
        matches = []
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.rstrip('\n\r')
                    
                    # Find all matches in the line
                    for match in pattern.finditer(line):
                        relative_path = str(file_path.relative_to(self.workspace_root))
                        matches.append({
                            'file': relative_path,
                            'line_number': line_num,
                            'line_content': line,
                            'match_start': match.start(),
                            'match_end': match.end(),
                            'matched_text': match.group(0)
                        })
        
        except Exception:
            # Skip files that can't be read
            pass
        
        return matches
    
    def _format_results(self, query: str, matches: List[Dict[str, Any]], has_more: bool) -> str:
        """Format search results for display."""
        if not matches:
            return f"No matches found for pattern: {query}"
        
        output = []
        output.append(f"Search results for pattern: {query}")
        output.append(f"Found {len(matches)} matches" + (" (truncated)" if has_more else ""))
        output.append("")
        
        # Group matches by file
        files_matches = {}
        for match in matches:
            file_path = match['file']
            if file_path not in files_matches:
                files_matches[file_path] = []
            files_matches[file_path].append(match)
        
        # Format output
        for file_path, file_matches in files_matches.items():
            output.append(f"📄 {file_path}")
            
            for match in file_matches:
                line_num = match['line_number']
                line_content = match['line_content']
                matched_text = match['matched_text']
                
                # Highlight the match (simple text highlighting)
                highlighted_line = line_content
                if matched_text:
                    highlighted_line = line_content.replace(
                        matched_text, f"**{matched_text}**", 1
                    )
                
                output.append(f"  {line_num}: {highlighted_line}")
            
            output.append("")
        
        if has_more:
            output.append(f"... and more matches (limit of {len(matches)} reached)")
        
        return '\n'.join(output)


def grep_search_tool(query: str, include_pattern: str = None, exclude_pattern: str = None, 
                    case_sensitive: bool = True) -> str:
    """
    Public function to perform grep-like search.
    
    Args:
        query: The regex pattern to search for
        include_pattern: Glob pattern for files to include
        exclude_pattern: Glob pattern for files to exclude
        case_sensitive: Whether the search should be case sensitive
        
    Returns:
        Formatted search results as a string
    """
    searcher = GrepSearch()
    return searcher.search(query, include_pattern, exclude_pattern, case_sensitive) 