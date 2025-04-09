import traceback
from io import StringIO
from contextlib import redirect_stdout
import ast
import asyncio
from crawl4ai import *
from google_search import search_for_llm


def find_last_valid_tag(s):
    pos = len(s)  # Start from the end of the string
    while True:
        # Find the next '>' before the current position
        pos = s.rfind('>', 0, pos)
        if pos == -1:
            return -1, None  # No valid tag found

        # Find the corresponding '<' before this '>'
        start = s.rfind('<', 0, pos)
        if start == -1:
            return -1, None

        # Extract the potential tag
        potential_tag = s[start:pos + 1]

        # If no spaces in the tag (excluding '<' and '>'), it’s valid
        if ' ' not in potential_tag[1:-1]:
            return start, potential_tag

        # Move position before this invalid tag to keep searching
        pos = start


def process_string(input_string):
    # Find the last valid tag
    tag_start, last_tag = find_last_valid_tag(input_string)
    if tag_start == -1:
        return input_string

    # Extract content after the tag
    tag_end = tag_start + len(last_tag) - 1
    content_after = input_string[tag_end + 1:]

    # Define tag handlers
    tag_handlers = {
        '<python>': {
            'execute': run_like_jupyter,
            'closing_tag': '</python>',
            'result_tag': 'python_result',
            'title': 'Code Executed'
        },
        '<search>': {
            'execute': search_for_llm,
            'closing_tag': '</search>',
            'result_tag': 'search_result',
            'title': 'Search Executed'
        },
        '<browser>': {
            'execute': browse_website,
            'closing_tag': '</browser>',
            'result_tag': 'browser_result',
            'title': 'Browser Executed'
        }
    }

    # Process the tag if it exists in handlers
    if last_tag in tag_handlers:
        # Remove last </think> if it exists before the tag
        last_end_think_start = input_string.rfind('</think>', 0, tag_start)
        if last_end_think_start != -1:
            input_string = input_string[:last_end_think_start] + input_string[last_end_think_start + len('</think>'):]

        handler = tag_handlers[last_tag]
        try:
            execution_result = handler['execute'](content_after)
        except Exception:
            execution_result = traceback.format_exc()

        # Construct the result
        return f"{input_string[:tag_start]}{last_tag}{content_after}{handler['closing_tag']}\n## {handler['title']}\n<{handler['result_tag']}>\n{execution_result}</{handler['result_tag']}>"

    return input_string


def modify_code_for_last_expression(code):
    """
    Modifies the code to print the result of the last expression if it's not a print statement.

    Args:
        code (str): The code string to modify.

    Returns:
        str: Modified code string.
    """
    tree = ast.parse(code)
    last_node = tree.body[-1] if tree.body else None
    if last_node and isinstance(last_node, ast.Expr):
        # Check if it's a print statement (Call to print function)
        if (isinstance(last_node.value, ast.Call) and
                isinstance(last_node.value.func, ast.Name) and
                last_node.value.func.id == 'print'):
            # It's a print statement, leave it as is
            pass
        else:
            # It's an expression that's not a print statement, modify to print its value
            last_expr = ast.parse(f'print({ast.unparse(last_node.value)})').body[0]
            tree.body[-1] = last_expr
    return ast.unparse(tree)


def run_like_jupyter(code):
    """
    Executes the code in a manner similar to Jupyter, capturing stdout and handling the last expression.

    Args:
        code (str): The code string to execute.

    Returns:
        str: Captured stdout output.
    """
    namespace = {}
    modified_code = modify_code_for_last_expression(code)

    captured_stdout = StringIO()
    with redirect_stdout(captured_stdout):
        try:
            exec(modified_code, namespace)
        except Exception as e:
            # Any exceptions will be caught by process_string and handled with traceback
            raise

    output = captured_stdout.getvalue()
    return output


def browse_website(url):
    return asyncio.run(async_browse_website(url))


async def async_browse_website(url):

    browser_config = BrowserConfig(
        headless=True,
        verbose=True,
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed", min_word_threshold=0)
        ),
        # markdown_generator=DefaultMarkdownGenerator(
        #     content_filter=BM25ContentFilter(user_query="WHEN_WE_FOCUS_BASED_ON_A_USER_QUERY", bm25_threshold=1.0)
        # ),
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url=url,
            config=run_config
        )
        return result.markdown


if __name__ == '__main__':
#     print(run_like_jupyter("""import urllib.request
#
# for i in range(5):
#     print(f"aaa{i}")"""))
    print(browse_website("https://livebench.ai/#/"))
