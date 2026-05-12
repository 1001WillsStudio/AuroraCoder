import ast
from io import StringIO
from contextlib import redirect_stdout


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