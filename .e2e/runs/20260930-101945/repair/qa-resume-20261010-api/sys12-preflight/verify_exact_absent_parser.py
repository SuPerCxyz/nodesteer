import ast
from pathlib import Path


SOURCE = Path(__file__).with_name("lifecycle_source_proposal.py")
CONTAINER_ID = "88a04edab1fa3f2ed05b885e05056981d499bdd8496b69f2961363b3ead2dd53"


class SafeStop(Exception):
    pass


tree = ast.parse(SOURCE.read_text())
definition = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "exact_absent")
module = ast.Module(body=[definition], type_ignores=[])
responses = []


def fake_docker(argv):
    assert argv == ["container", "inspect", CONTAINER_ID]
    return responses.pop(0)


scope = {"SafeStop": SafeStop, "docker": fake_docker}
exec(compile(module, str(SOURCE), "exec"), scope)
exact_absent = scope["exact_absent"]


def accepts(label, response):
    responses.append(response)
    exact_absent("container", CONTAINER_ID)
    print(label + "=PASS_ACCEPTED")


def rejects(label, response, reason):
    responses.append(response)
    try:
        exact_absent("container", CONTAINER_ID)
    except SafeStop as exc:
        if str(exc) != reason:
            raise AssertionError(label + " returned wrong SafeStop")
        print(label + "=PASS_REJECTED")
        return
    raise AssertionError(label + " was incorrectly accepted")


rejects("inspect_rc0_collision", (0, "[]", ""), "exact_resource_name_collision")
accepts("exact_no_such_container_suffix", (1, "", "Error response from daemon: No such container: " + CONTAINER_ID))
rejects("wrong_container_id", (1, "", "Error response from daemon: No such container: " + "0" * 64),
        "exact_resource_name_unknown")
rejects("permission_error_mentions_exact_id", (1, "", "permission denied while inspecting " + CONTAINER_ID),
        "exact_resource_name_unknown")
rejects("other_error_mentions_exact_id", (1, "", "daemon inspect failed for " + CONTAINER_ID),
        "exact_resource_name_unknown")
if responses:
    raise AssertionError("test response queue not drained")
