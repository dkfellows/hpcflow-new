from hpcflow.app import app as hf


def test_merge_template_level_resources_into_element_set():
    wkt = hf.WorkflowTemplate(
        name="w1",
        tasks=[hf.Task(schemas=[hf.task_schemas.test_t1_ps])],
        resources={"any": {"num_cores": 1}},
    )
    assert wkt.tasks[0].element_sets[0].resources == hf.ResourceList.from_json_like(
        {"any": {"num_cores": 1}}
    )