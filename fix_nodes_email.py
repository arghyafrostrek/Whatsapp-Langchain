import ast
funcs = {
    'get_session_history', 'save_message', 'get_full_history', 
    'get_sessions_by_email', 'get_all_sessions_summary', 
    'get_session_state', 'save_session_state', 'get_conversation_summary', 
    'update_conversation_summary', 'get_user_profile', 'update_user_profile', 
    'set_pending_action', 'get_pending_action', 'clear_pending_action'
}
with open('nodes.py', 'r', encoding='utf-8') as f:
    orig = f.read()
tree = ast.parse(orig)
class Locator(ast.NodeVisitor):
    def __init__(self):
        self.calls = []
    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id in funcs:
            if not any(k.arg == 'tenant_id' for k in node.keywords):
                self.calls.append(node)
locator = Locator()
locator.visit(tree)
print(f"Found {len(locator.calls)} function calls to update.")
lines = orig.split('\n')
calls = sorted(locator.calls, key=lambda n: (n.end_lineno, n.end_col_offset), reverse=True)
for call in calls:
    l_end, c_end = call.end_lineno - 1, call.end_col_offset
    line = lines[l_end]
    needs_comma = False
    if call.args or call.keywords:
        needs_comma = True
    insertion = ", tenant_id='default'" if needs_comma else "tenant_id='default'"
    idx = c_end - 1
    while idx >= 0 and line[idx] != ')':
        idx -= 1
    if idx >= 0:
        lines[l_end] = line[:idx] + insertion + line[idx:]
with open('nodes.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('SUCCESS!')
