"""Request-local Hermbot schema projection; canonical Hermes schemas remain untouched."""
from copy import deepcopy


def filter_tools(schemas, admission):
    if 'error' in admission or 'capabilities' not in admission:
        return []
    allowed = set(admission['capabilities']['allowed_tools'])
    result = []
    for schema in schemas or []:
        name = schema.get('function', schema).get('name')
        if name not in allowed:
            continue
        if name == 'hermbot_review' and admission['capabilities']['preparation_required']:
            schema = deepcopy(schema)
            parameters = schema.get('function', schema)['parameters']
            parameters['properties']['kind']['enum'] = ['input']
            parameters['required'] = list(dict.fromkeys(parameters.get('required', []) + ['kind']))
        result.append(schema)
    return result


def filter_request(request, admission):
    """Copy provider kwargs; execution middleware remains the admission boundary."""
    result = deepcopy(request)
    tools = filter_tools(result.get('tools', []), admission)
    if tools:
        result['tools'] = tools
        choice = result.get('tool_choice')
        if isinstance(choice, dict):
            name = choice.get('function', choice).get('name')
            if name and name not in {tool.get('function', tool).get('name') for tool in tools}:
                result.pop('tool_choice', None)
    else:
        result.pop('tools', None)
        result.pop('tool_choice', None)
        result.pop('parallel_tool_calls', None)
    return result
