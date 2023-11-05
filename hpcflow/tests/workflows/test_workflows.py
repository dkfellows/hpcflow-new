from importlib import resources
import os
import sys
import time
import pytest
from hpcflow.app import app as hf
from hpcflow.sdk.core.actions import EARStatus
from hpcflow.sdk.core.test_utils import (
    P1_parameter_cls as P1,
    P1_sub_parameter_cls as P1_sub,
)


@pytest.mark.integration
def test_workflow_1(tmp_path, new_null_config):
    package = "hpcflow.tests.data"
    with resources.path(package=package, resource="workflow_1.yaml") as path:
        wk = hf.Workflow.from_YAML_file(YAML_path=path, path=tmp_path)
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks[0].elements[0].outputs.p2.value == "201"


@pytest.mark.integration
def test_workflow_1_with_working_dir_with_spaces(tmp_path, new_null_config):
    workflow_dir = tmp_path / "sub path with spaces"
    workflow_dir.mkdir()
    package = "hpcflow.tests.data"
    with resources.path(package=package, resource="workflow_1.yaml") as path:
        wk = hf.Workflow.from_YAML_file(YAML_path=path, path=workflow_dir)
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks[0].elements[0].outputs.p2.value == "201"


@pytest.mark.integration
@pytest.mark.skip(
    reason="Sometimes fails on MacOS GHAs runner; too slow on Windows + Linux"
)
def test_run_abort(tmp_path, new_null_config):
    package = "hpcflow.tests.data"
    with resources.path(package=package, resource="workflow_test_run_abort.yaml") as path:
        wk = hf.Workflow.from_YAML_file(YAML_path=path, path=tmp_path)
    wk.submit(add_to_known=False)

    # wait for the run to start;
    # TODO: instead of this: we should add a `wait_to_start=RUN_ID` method to submit()
    max_wait_iter = 15
    aborted = False
    for _ in range(max_wait_iter):
        time.sleep(4)
        try:
            wk.abort_run()  # single task and element so no need to disambiguate
        except ValueError:
            continue
        else:
            aborted = True
            break
    if not aborted:
        raise RuntimeError("Could not abort the run")

    wk.wait()
    assert wk.tasks[0].outputs.is_finished[0].value == "true"


@pytest.mark.integration
@pytest.mark.parametrize("store", ["json", "zarr"])
def test_multi_command_action_stdout_parsing(null_config, tmp_path, store):
    if os.name == "nt":
        cmds = [
            "Write-Output (<<parameter:p1>> + 100)",
            "Write-Output (<<parameter:p1>> + 200)",
        ]
    else:
        cmds = [
            'echo "$((<<parameter:p1>> + 100))"',
            'echo "$((<<parameter:p1>> + 200))"',
        ]
    act = hf.Action(
        commands=[
            hf.Command(
                command=cmds[0],
                stdout="<<int(parameter:p2)>>",
            ),
            hf.Command(
                command=cmds[1],
                stdout="<<float(parameter:p3)>>",
            ),
        ]
    )
    s1 = hf.TaskSchema(
        objective="t1",
        actions=[act],
        inputs=[hf.SchemaInput("p1")],
        outputs=[hf.SchemaOutput("p2"), hf.SchemaOutput("p3")],
    )
    t1 = hf.Task(schema=[s1], inputs=[hf.InputValue("p1", 1)])
    wk = hf.Workflow.from_template_data(
        tasks=[t1],
        template_name="wk2",
        path=tmp_path,
        store=store,
    )
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks.t1.elements[0].get("outputs") == {"p2": 101, "p3": 201.0}


@pytest.mark.integration
@pytest.mark.parametrize("store", ["json", "zarr"])
def test_element_get_group(null_config, tmp_path, store):
    if os.name == "nt":
        cmd = "Write-Output (<<parameter:p1c>> + 100)"
    else:
        cmd = 'echo "$((<<parameter:p1c>> + 100))"'
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter="p1c")],
        outputs=[hf.SchemaOutput(parameter="p1c")],
        actions=[
            hf.Action(
                commands=[
                    hf.Command(
                        command=cmd,
                        stdout="<<parameter:p1c.CLI_parse()>>",
                    )
                ],
            ),
        ],
        parameter_class_modules=["hpcflow.sdk.core.test_utils"],
    )
    s2 = hf.TaskSchema(
        objective="t2",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1c"), group="my_group")],
    )

    t1 = hf.Task(
        schema=s1,
        inputs=[hf.InputValue("p1c", value=P1(a=10, sub_param=P1_sub(e=5)))],
        sequences=[hf.ValueSequence("inputs.p1c.a", values=[20, 30], nesting_order=0)],
        groups=[hf.ElementGroup(name="my_group")],
    )
    t2 = hf.Task(
        schema=s2,
        nesting_order={"inputs.p1c": 0},
    )
    wk = hf.Workflow.from_template_data(
        tasks=[t1, t2],
        template_name="w1",
        path=tmp_path,
        store=store,
    )
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks.t2.num_elements == 1
    assert wk.tasks.t2.elements[0].get("inputs.p1c") == [P1(a=120), P1(a=130)]


@pytest.mark.integration
def test_element_get_sub_object_group(null_config, tmp_path):
    if os.name == "nt":
        cmd = "Write-Output (<<parameter:p1c>> + 100)"
    else:
        cmd = 'echo "$((<<parameter:p1c>> + 100))"'
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter="p1c")],
        outputs=[hf.SchemaOutput(parameter="p1c")],
        actions=[
            hf.Action(
                commands=[
                    hf.Command(
                        command=cmd,
                        stdout="<<parameter:p1c.CLI_parse(e=10)>>",
                    )
                ],
            ),
        ],
        parameter_class_modules=["hpcflow.sdk.core.test_utils"],
    )
    s2 = hf.TaskSchema(
        objective="t2",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1c"), group="my_group")],
    )

    t1 = hf.Task(
        schema=s1,
        inputs=[hf.InputValue("p1c", value=P1(a=10, sub_param=P1_sub(e=5)))],
        sequences=[hf.ValueSequence("inputs.p1c.a", values=[20, 30], nesting_order=0)],
        groups=[hf.ElementGroup(name="my_group")],
    )
    t2 = hf.Task(
        schema=s2,
        nesting_order={"inputs.p1c": 0},
    )
    wk = hf.Workflow.from_template_data(
        tasks=[t1, t2],
        template_name="w1",
        path=tmp_path,
    )
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks.t2.num_elements == 1
    assert wk.tasks.t2.elements[0].get("inputs.p1c.sub_param") == [
        P1_sub(e=10),
        P1_sub(e=10),
    ]


@pytest.mark.integration
def test_element_get_sub_data_group(null_config, tmp_path):
    if os.name == "nt":
        cmd = "Write-Output (<<parameter:p1c>> + 100)"
    else:
        cmd = 'echo "$((<<parameter:p1c>> + 100))"'
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter="p1c")],
        outputs=[hf.SchemaOutput(parameter="p1c")],
        actions=[
            hf.Action(
                commands=[
                    hf.Command(
                        command=cmd,
                        stdout="<<parameter:p1c.CLI_parse(e=10)>>",
                    )
                ],
            ),
        ],
        parameter_class_modules=["hpcflow.sdk.core.test_utils"],
    )
    s2 = hf.TaskSchema(
        objective="t2",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1c"), group="my_group")],
    )

    t1 = hf.Task(
        schema=s1,
        inputs=[hf.InputValue("p1c", value=P1(a=10, sub_param=P1_sub(e=5)))],
        sequences=[hf.ValueSequence("inputs.p1c.a", values=[20, 30], nesting_order=0)],
        groups=[hf.ElementGroup(name="my_group")],
    )
    t2 = hf.Task(
        schema=s2,
        nesting_order={"inputs.p1c": 0},
    )
    wk = hf.Workflow.from_template_data(
        tasks=[t1, t2],
        template_name="w1",
        path=tmp_path,
    )
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks.t2.num_elements == 1
    assert wk.tasks.t2.elements[0].get("inputs.p1c.a") == [120, 130]


@pytest.mark.integration
def test_input_source_labels_and_groups(null_config, tmp_path):
    """This is structurally the same as the `fit_yield_functions` MatFlow workflow."""
    if os.name == "nt":
        cmds = [
            "Write-Output (<<parameter:p1>> + 100)",
            "Write-Output (<<parameter:p2[one]>> + <<sum(parameter:p2[two])>>)",
        ]
    else:
        cmds = [
            'echo "$((<<parameter:p1>> + 100))"',
            'echo "$((<<parameter:p2[one]>> + <<sum(parameter:p2[two])>>))"',
        ]
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
    )
    s2 = hf.TaskSchema(
        objective="t2",
        inputs=[hf.SchemaInput(parameter="p1")],
        outputs=[hf.SchemaInput(parameter="p2")],
        actions=[
            hf.Action(
                commands=[hf.Command(command=cmds[0], stdout="<<int(parameter:p2)>>")]
            )
        ],
    )
    s3 = hf.TaskSchema(
        objective="t3",
        inputs=[
            hf.SchemaInput(
                parameter="p2",
                multiple=True,
                labels={"one": {}, "two": {"group": "my_group"}},
            ),
        ],
        outputs=[hf.SchemaInput(parameter="p3")],
        actions=[
            hf.Action(
                commands=[hf.Command(command=cmds[1], stdout="<<int(parameter:p3)>>")]
            )
        ],
    )
    tasks = [
        hf.Task(
            schema=s1,
            element_sets=[
                hf.ElementSet(inputs=[hf.InputValue("p1", 1)]),
                hf.ElementSet(
                    sequences=[
                        hf.ValueSequence(
                            path="inputs.p1",
                            values=[2, 3, 4],
                            nesting_order=0,
                        ),
                    ],
                    groups=[hf.ElementGroup(name="my_group")],
                ),
            ],
        ),
        hf.Task(
            schema=s2,
            nesting_order={"inputs.p1": 0},
        ),
        hf.Task(
            schema=s3,
            input_sources={
                "p2[one]": [
                    hf.InputSource.task(
                        task_ref=1,
                        where=hf.Rule(path="inputs.p1", condition={"value.equal_to": 1}),
                    )
                ],
                "p2[two]": [
                    hf.InputSource.task(
                        task_ref=1,
                        where=hf.Rule(
                            path="inputs.p1", condition={"value.not_equal_to": 1}
                        ),
                    )
                ],
            },
        ),
    ]
    wk = hf.Workflow.from_template_data(
        tasks=tasks,
        path=tmp_path,
        template_name="wk0",
    )
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks.t2.num_elements == 4
    assert wk.tasks.t3.num_elements == 1
    assert wk.tasks.t3.elements[0].outputs.p3.value == 410


@pytest.mark.integration
def test_loop_simple(null_config, tmp_path):
    if os.name == "nt":
        cmd = "Write-Output (<<parameter:p1>> + 100)"
    else:
        cmd = 'echo "$((<<parameter:p1>> + 100))"'
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
        outputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
        actions=[
            hf.Action(commands=[hf.Command(command=cmd, stdout="<<int(parameter:p1)>>")]),
        ],
    )
    wk = hf.Workflow.from_template_data(
        tasks=[hf.Task(schema=s1, inputs=[hf.InputValue("p1", value=1)])],
        loops=[hf.Loop(tasks=[0], num_iterations=3)],
        path=tmp_path,
        template_name="wk0",
    )
    wk.submit(wait=True, add_to_known=False)
    assert wk.tasks.t1.elements[0].get("outputs.p1") == 301


@pytest.mark.integration
def test_loop_termination_multi_element(null_config, tmp_path):
    if os.name == "nt":
        cmds = [
            "Write-Output (<<parameter:p1>> + 100)",
            "Write-Output 'Hello from the second action!'",
        ]
    else:
        cmds = [
            'echo "$((<<parameter:p1>> + 100))"',
            'echo "Hello from the second action!"',
        ]
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
        outputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
        actions=[
            hf.Action(
                commands=[hf.Command(command=cmds[0], stdout="<<int(parameter:p1)>>")]
            ),
            hf.Action(commands=[hf.Command(command=cmds[1])]),
        ],
    )
    tasks = [
        hf.Task(
            schema=s1,
            sequences=[hf.ValueSequence("inputs.p1", values=[1, 2], nesting_order=0)],
        ),
    ]
    wk = hf.Workflow.from_template_data(
        tasks=tasks,
        loops=[
            hf.Loop(
                tasks=[0],
                num_iterations=3,
                termination=hf.Rule(
                    path="outputs.p1", condition={"value.greater_than": 201}
                ),
            )
        ],
        path=tmp_path,
        template_name="wk0",
    )
    wk.submit(wait=True, add_to_known=False)
    elem_0 = wk.tasks.t1.elements[0]
    elem_1 = wk.tasks.t1.elements[1]

    # all three iterations needed for first element:
    assert elem_0.iterations[0].action_runs[0].status is EARStatus.success
    assert elem_0.iterations[1].action_runs[0].status is EARStatus.success
    assert elem_0.iterations[2].action_runs[0].status is EARStatus.success

    # only first two iterations needed for second element:
    assert elem_1.iterations[0].action_runs[0].status is EARStatus.success
    assert elem_1.iterations[1].action_runs[0].status is EARStatus.success
    assert elem_1.iterations[2].action_runs[0].status is EARStatus.skipped
