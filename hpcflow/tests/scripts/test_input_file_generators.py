import os
import time

import pytest
from hpcflow.app import app as hf


@pytest.mark.integration
@pytest.mark.skipif("hf.run_time_info.is_frozen")
def test_input_file_generator_creates_file(null_config, tmp_path):

    inp_file = hf.FileSpec(label="my_input_file", name="my_input_file.txt")

    if os.name == "nt":
        cmd = "Get-Content <<file:my_input_file>>"
    else:
        cmd = "cat <<file:my_input_file>>"

    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
        actions=[
            hf.Action(
                commands=[hf.Command(cmd)],
                input_file_generators=[
                    hf.InputFileGenerator(
                        input_file=inp_file,
                        inputs=[hf.Parameter("p1")],
                        script="<<script:input_file_generator_basic.py>>",
                    ),
                ],
                environments=[hf.ActionEnvironment(environment="python_env")],
            )
        ],
    )
    p1_val = 101
    t1 = hf.Task(schema=s1, inputs={"p1": p1_val})
    wk = hf.Workflow.from_template_data(
        tasks=[t1],
        template_name="input_file_generator_test",
        path=tmp_path,
    )
    wk.submit(wait=True, add_to_known=False)
    # TODO: investigate why the value is not always populated on GHA Ubuntu runners (tends
    # to be later Python versions):
    time.sleep(10)

    # check the input file is written
    inp_file_path = wk.execution_path / f"task_0_t1/e_0/r_0/{inp_file.name.name}"
    inp_file_contents = inp_file_path.read_text()
    assert inp_file_contents.strip() == str(p1_val)

    # check the command successfully printed the file contents to stdout:
    std_out = wk.submissions[0].jobscripts[0].direct_stdout_path.read_text()
    assert std_out.strip() == str(p1_val)


@pytest.mark.integration
@pytest.mark.skipif("hf.run_time_info.is_frozen")
def test_IFG_std_stream_redirect_on_exception(new_null_config, tmp_path):
    """Test exceptions raised by the app during execution of a IFG script are printed to the
    std-stream redirect file (and not the jobscript's standard error file)."""

    # define a custom python environment which redefines the `WK_PATH` shell variable to
    # a nonsense value so the app cannot load the workflow and thus raises an exception

    if os.name == "nt":
        env_cmd = '$WK_PATH = "nonsense_path"'
    else:
        env_cmd = 'WK_PATH="nonsense_path"'

    env_cmd += "; python <<script_name>> <<args>>"
    bad_env = hf.Environment(
        name="bad_python_env",
        executables=[
            hf.Executable(
                label="python_script",
                instances=[
                    hf.ExecutableInstance(
                        command=env_cmd,
                        num_cores=1,
                        parallel_mode=None,
                    )
                ],
            )
        ],
    )
    hf.envs.add_object(bad_env, skip_duplicates=True)

    inp_file = hf.FileSpec(label="my_input_file", name="my_input_file.txt")
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
        actions=[
            hf.Action(
                input_file_generators=[
                    hf.InputFileGenerator(
                        input_file=inp_file,
                        inputs=[hf.Parameter("p1")],
                        script="<<script:input_file_generator_basic.py>>",
                    ),
                ],
                environments=[hf.ActionEnvironment(environment="bad_python_env")],
            )
        ],
    )

    p1_val = 101
    t1 = hf.Task(schema=s1, inputs={"p1": p1_val})
    wk = hf.Workflow.from_template_data(
        tasks=[t1], template_name="input_file_generator_test", path=tmp_path
    )
    wk.submit(wait=True, add_to_known=False, status=False)
    # TODO: investigate why the value is not always populated on GHA Ubuntu runners (tends
    # to be later Python versions):
    time.sleep(10)

    # jobscript stderr should be empty
    assert not wk.submissions[0].jobscripts[0].direct_stderr_path.read_text()

    # std stream file has workflow not found traceback
    std_stream_path = wk.execution_path / "task_0_t1/e_0/r_0/hpcflow_std.txt"
    assert std_stream_path.is_file()
    assert "WorkflowNotFoundError" in std_stream_path.read_text()


@pytest.mark.integration
@pytest.mark.skipif("hf.run_time_info.is_frozen")
def test_IFG_std_out_std_err_not_redirected(null_config, tmp_path):
    """Test that standard error and output streams from an IFG script are written to the jobscript
    standard error and output files."""
    inp_file = hf.FileSpec(label="my_input_file", name="my_input_file.txt")
    s1 = hf.TaskSchema(
        objective="t1",
        inputs=[hf.SchemaInput(parameter=hf.Parameter("p1"))],
        actions=[
            hf.Action(
                input_file_generators=[
                    hf.InputFileGenerator(
                        input_file=inp_file,
                        inputs=[hf.Parameter("p1")],
                        script="<<script:input_file_generator_test_stdout_stderr.py>>",
                    ),
                ],
                environments=[hf.ActionEnvironment(environment="python_env")],
            )
        ],
    )
    p1_val = 101
    stdout_msg = str(p1_val)
    stderr_msg = str(p1_val)
    t1 = hf.Task(schema=s1, inputs={"p1": p1_val})
    wk = hf.Workflow.from_template_data(
        tasks=[t1],
        template_name="input_file_generator_test",
        path=tmp_path,
    )
    wk.submit(wait=True, add_to_known=False)
    # TODO: investigate why the value is not always populated on GHA Ubuntu runners (tends
    # to be later Python versions):
    time.sleep(10)

    std_out = wk.submissions[0].jobscripts[0].direct_stdout_path.read_text()
    std_err = wk.submissions[0].jobscripts[0].direct_stderr_path.read_text()

    assert std_out.strip() == stdout_msg
    assert std_err.strip() == stderr_msg