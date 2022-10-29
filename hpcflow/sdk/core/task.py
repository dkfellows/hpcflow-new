from __future__ import annotations
from dataclasses import dataclass
from optparse import Option
from typing import Dict, List, Optional, Tuple, Union

from hpcflow.sdk.core.json_like import ChildObjectSpec, JSONLike


from .command_files import FileSpec
from .element import Element, ElementFilter, ElementGroup
from .errors import (
    MissingInputs,
    TaskTemplateInvalidNesting,
    TaskTemplateMultipleInputValues,
    TaskTemplateMultipleSchemaObjectives,
    TaskTemplateUnexpectedInput,
)
from .object_list import GroupList, index
from .parameters import (
    InputSource,
    InputSourceMode,
    InputSourceType,
    InputValue,
    ParameterPath,
    ResourceSpec,
    SchemaInput,
    SchemaOutput,
    TaskSourceType,
    ValuePerturbation,
    ValueSequence,
)
from .utils import get_duplicate_items, get_item_repeat_index


class Task(JSONLike):
    """Parametrisation of an isolated task for which a subset of input values are given
    "locally". The remaining input values are expected to be satisfied by other
    tasks/imports in the workflow."""

    __slots__ = (
        "_schemas",
        "_repeats",
        "_resources",
        "_inputs",
        "_input_files",
        "_input_file_generator_sources",
        "_output_file_parser_sources",
        "_perturbations",
        "_sequences",
        "_input_sources",
        "_input_source_mode",
        "_nesting_order",
        "_groups",
        "_name",
        "_defined_input_types",  # assigned in _validate()
        "workflow_template",
        "_insert_ID",
        "_dir_name",
    )

    app = None
    _child_objects = (
        ChildObjectSpec(
            name="schemas",
            class_name="TaskSchema",
            is_multiple=True,
            shared_data_name="task_schemas",
            shared_data_primary_key="name",
        ),
        ChildObjectSpec(
            name="inputs",
            class_name="InputValue",
            is_multiple=True,
            dict_key_attr="parameter",
            dict_val_attr="value",
            parent_ref="_task",
        ),
        ChildObjectSpec(
            name="resources",
            class_name="ResourceSpec",
            parent_ref="_task",
        ),
        ChildObjectSpec(
            name="sequences",
            class_name="ValueSequence",
            is_multiple=True,
        ),
        ChildObjectSpec(
            name="input_sources",
            class_name="InputSource",
            is_multiple=True,
            is_dict_values=True,
            is_dict_values_ensure_list=True,
        ),
        ChildObjectSpec(
            name="input_source_mode",
            class_name="InputSourceMode",
            is_enum=True,
        ),
    )

    def __init__(
        self,
        schemas: Union[TaskSchema, str, List[TaskSchema], List[str]],
        repeats: Optional[Union[int, List[int]]] = 1,
        resources: Optional[Dict[str, Dict]] = None,
        inputs: Optional[List[InputValue]] = None,
        input_files: Optional[List[FileSpec]] = None,
        input_file_generator_sources: Optional[List] = None,
        output_file_parser_sources: Optional[List] = None,
        perturbations: Optional[List[ValuePerturbation]] = None,
        sequences: Optional[List[ValueSequence]] = None,
        input_sources: Optional[Dict[str, InputSource]] = None,
        input_source_mode: Optional[Union[str, InputSourceType]] = None,
        nesting_order: Optional[List] = None,
        groups: Optional[List[ElementGroup]] = None,
        workflow_template=None,
        insert_ID=None,
        dir_name=None,
    ):
        # TODO: modify from_JSON_like(?) so "internal" attributes are not in init

        """
        Parameters
        ----------
        schema
            A (list of) `TaskSchema` object(s) and/or a (list of) strings that are task
            schema names that uniquely identify a task schema. If strings are provided,
            the `TaskSchema` object will be fetched from the known task schemas loaded by
            the app configuration.

        """

        # TODO: allow init via specifying objective and/or method and/or implementation
        # (lists of) strs e.g.: Task(
        #   objective='simulate_VE_loading',
        #   method=['CP_FFT', 'taylor'],
        #   implementation=['damask', 'damask']
        # )
        # where method and impl must be single strings of lists of the same length
        # and method/impl are optional/required only if necessary to disambiguate
        #
        # this would be like Task(schemas=[
        #   'simulate_VE_loading_CP_FFT_damask',
        #   'simulate_VE_loading_taylor_damask'
        # ])

        if not isinstance(schemas, list):
            schemas = [schemas]

        _schemas = []
        for i in schemas:
            if isinstance(i, str):
                try:
                    i = self.app.TaskSchema.get_by_key(i)
                except KeyError:
                    raise KeyError(f"TaskSchema {i!r} not found.")
            elif not isinstance(i, self.app.TaskSchema):
                raise TypeError(f"Not a TaskSchema object: {i!r}")
            _schemas.append(i)

        self._schemas = _schemas
        self._repeats = repeats
        self._resources = resources or self.app.ResourceSpec(
            main={}
        )  # TODO: use action names from schemas
        self._inputs = inputs or []
        self._input_files = input_files or []
        self._input_file_generator_sources = input_file_generator_sources or []
        self._output_file_parser_sources = output_file_parser_sources or []
        self._perturbations = perturbations or []
        self._sequences = sequences or []
        self._input_sources = input_sources or {}
        self._input_source_mode = input_source_mode or (
            InputSourceMode.MANUAL if input_sources else InputSourceMode.AUTO
        )
        self._nesting_order = nesting_order or {}
        self._groups = GroupList(groups or [])

        self._validate()
        self._name = self._get_name()
        self._insert_ID = insert_ID
        self._dir_name = dir_name

        self.workflow_template = None

    def __repr__(self):
        return f"{self.__class__.__name__}(" f"name={self.name!r}" f")"

    def to_dict(self):
        return {
            k.lstrip("_"): getattr(self, k)
            for k in self.__slots__
            if k not in ["_name", "_defined_input_types"]
        }

    def _validate(self):

        # TODO: check a nesting order specified for each sequence?

        names = set(i.objective.name for i in self.schemas)
        if len(names) > 1:
            raise TaskTemplateMultipleSchemaObjectives(
                f"All task schemas used within a task must have the same "
                f"objective, but found multiple objectives: {list(names)!r}"
            )

        input_types = [i.parameter.typ for i in self.get_non_sub_parameter_input_values()]
        dup_params = get_duplicate_items(input_types)
        if dup_params:
            raise TaskTemplateMultipleInputValues(
                f"The following parameters are associated with multiple input value "
                f"definitions: {dup_params!r}."
            )

        unexpected_types = set(input_types) - self.all_schema_input_types
        if unexpected_types:
            raise TaskTemplateUnexpectedInput(
                f"The following input parameters are unexpected: {list(unexpected_types)!r}"
            )

        for i in self.sequences:
            self._nesting_order.update({".".join(i.path): i.nesting_order})

        for k, v in self.nesting_order.items():
            if v < 0:
                raise TaskTemplateInvalidNesting(
                    f"`nesting_order` must be >=0 for all keys, but for key {k!r}, value "
                    f"of {v!r} was specified."
                )

        self._defined_input_types = set(input_types)

    def _get_name(self):
        out = f"{self.objective.name}"
        for idx, schema_i in enumerate(self.schemas, start=1):
            need_and = idx < len(self.schemas) and (
                self.schemas[idx].method or self.schemas[idx].implementation
            )
            out += (
                f"{f'_{schema_i.method}' if schema_i.method else ''}"
                f"{f'_{schema_i.implementation}' if schema_i.implementation else ''}"
                f"{f'_and' if need_and else ''}"
            )
        return out

    @staticmethod
    def get_task_unique_names(tasks: List[Task]):
        """Get the unique name of each in a list of tasks.

        Returns
        -------
        list of str

        """

        task_name_rep_idx = get_item_repeat_index(
            tasks,
            item_callable=lambda x: x.name,
            distinguish_singular=True,
        )

        names = []
        for idx, task in enumerate(tasks):
            add_rep = f"_{task_name_rep_idx[idx]}" if task_name_rep_idx[idx] > 0 else ""
            names.append(f"{task.name}{add_rep}")

        return names

    def _get_nesting_order(self, seq):
        """Find the nesting order for a task sequence."""
        return self.nesting_order[seq._get_param_path()] if len(seq.values) > 1 else -1

    def make_persistent(self, workflow):
        """Add all task input data to a persistent workflow and return a record of the
        Zarr parameter group indices for each bit of task data."""

        input_data_indices = {}

        input_data_indices.update(self.resources.make_persistent(workflow))

        for inp_i in self.inputs:
            input_data_indices.update(inp_i.make_persistent(workflow))

        for seq_i in self.sequences:
            input_data_indices.update(seq_i.make_persistent(workflow))

        return input_data_indices

    def _prepare_persistent_outputs(self, workflow, num_elements):
        # TODO: check that schema is present when adding task? (should this be here?)
        output_data_indices = {}
        for schema in self.schemas:
            for output in schema.outputs:
                output_data_indices[output.typ] = []
                for i in range(num_elements):
                    group_idx = workflow._add_parameter_group(data=None, is_set=False)
                    output_data_indices[output.typ].append(group_idx)

        return output_data_indices

    def prepare_element_resolution(self):

        multiplicities = [
            {
                "multiplicity": 1,
                "nesting_order": -1,
                "address": self.resources._get_param_path(),
            }
        ]

        for inp_i in self.inputs:
            multiplicities.append(
                {
                    "multiplicity": 1,
                    "nesting_order": -1,
                    "address": inp_i._get_param_path(),
                }
            )

        for seq_i in self.sequences:
            multiplicities.append(
                {
                    "multiplicity": len(seq_i.values),
                    "nesting_order": self._get_nesting_order(seq_i),
                    "address": seq_i._get_param_path(),
                }
            )

        return multiplicities

    @property
    def index(self):
        if self.workflow_template:
            return self.workflow_template.tasks.index(self)
        else:
            return None

    @classmethod
    def from_spec(cls, spec, all_schemas, all_parameters):
        key = (
            spec.pop("objective"),
            spec.pop("method", None),
            spec.pop("implementation", None),
        )  # TODO: maybe don't mutate spec?
        spec["schemas"] = all_schemas[
            key
        ]  # TODO parse multiple methods/impl not multiple schemas

        sequences = spec.pop("sequences", [])
        inputs_spec = spec.pop("inputs", [])
        input_sources_spec = spec.pop("input_sources", {})
        perturbs_spec = spec.pop("perturbations", {})
        nesting_order = spec.pop("nesting_order", {})

        for k in list(nesting_order.keys()):
            new_k = tuple(k.split("."))
            nesting_order[new_k] = nesting_order.pop(k)

        print(f"nesting_order: {nesting_order}")

        inputs = []
        if isinstance(inputs_spec, dict):
            # transform to a list:
            for input_path, input_val in inputs_spec.items():
                is_sequence = input_path.endswith("[]")
                if is_sequence:
                    input_path = input_path.split("[]")[0]
                input_path = input_path.split(".")
                inputs.append(
                    {
                        "parameter": input_path[0],
                        "path": input_path[1:],
                        "value": input_val[0] if is_sequence else input_val,
                    }
                )
                if is_sequence:
                    seq_path = ["inputs"] + input_path
                    sequences.append(
                        {
                            "path": seq_path,
                            "values": input_val,
                            "nesting_order": nesting_order.get(tuple(seq_path)),
                        }
                    )
        else:
            inputs = inputs_spec

        # add any nesting orders that are defined in the sequences:
        for i in sequences:
            if "nesting_order" in i:
                nesting_order.update({tuple(i["path"]): i["nesting_order"]})

        perturbs = [{"name": p_name, **p} for p_name, p in perturbs_spec.items()]

        spec.update(
            {
                "inputs": [InputValue.from_spec(i, all_parameters) for i in inputs],
                "sequences": [ValueSequence.from_spec(i) for i in sequences],
                "perturbations": [ValuePerturbation.from_spec(i) for i in perturbs],
                "nesting_order": nesting_order,
                "input_sources": {
                    i: [InputSource.from_spec(j) for j in sources]
                    for i, sources in input_sources_spec.items()
                },
            }
        )

        return cls(**spec)

    def get_available_task_input_sources(
        self, source_tasks: Optional[List[Task]] = None
    ) -> List[InputSource]:
        """For each input parameter of this task, generate a list of possible input sources
        that derive from inputs or outputs of this and other provided tasks.

        Note this only produces a subset of available input sources for each input
        parameter; other available input sources may exist from workflow imports."""

        available = {}
        for schema_input in self.all_schema_inputs:
            available[schema_input.typ] = []

            for src_task_idx, src_task_i in enumerate(source_tasks or []):

                for param_i in src_task_i.provides_parameters:

                    if param_i.typ == schema_input.typ:

                        available[schema_input.typ].append(
                            self.app.InputSource(
                                source_type=self.app.InputSourceType.TASK,
                                task_ref=src_task_i.insert_ID,
                                task_source_type={
                                    "SchemaInput": self.app.TaskSourceType.INPUT,
                                    "SchemaOutput": self.app.TaskSourceType.OUTPUT,
                                }[
                                    param_i.__class__.__name__
                                ],  # TODO: make nicer
                            )
                        )

            if schema_input.typ in self.defined_input_types:
                available[schema_input.typ].append(self.app.InputSource.local())

            if schema_input.default_value is not None:
                available[schema_input.typ].append(self.app.InputSource.default())

        return available

    @property
    def schemas(self):
        return self._schemas

    @property
    def repeats(self):
        return self._repeats

    @property
    def resources(self):
        return self._resources

    @property
    def inputs(self):
        return self._inputs

    @property
    def input_files(self):
        return self._input_files

    @property
    def input_file_generator_sources(self):
        return self._input_file_generator_sources

    @property
    def output_file_parser_sources(self):
        return self._output_file_parser_sources

    @property
    def perturbations(self):
        return self._perturbations

    @property
    def sequences(self):
        return self._sequences

    @property
    def input_sources(self):
        return self._input_sources

    @property
    def input_source_mode(self):
        return self._input_source_mode

    @property
    def insert_ID(self):
        return self._insert_ID

    @property
    def dir_name(self):
        "Artefact directory name."
        return self._dir_name

    @property
    def nesting_order(self):
        return {tuple(k.split(".")): v for k, v in self._nesting_order.items()}

    @property
    def groups(self):
        return self._groups

    @property
    def name(self):
        return self._name

    @property
    def objective(self):
        return self.schemas[0].objective

    @property
    def all_schema_inputs(self) -> Tuple[SchemaInput]:
        return tuple(inp_j for schema_i in self.schemas for inp_j in schema_i.inputs)

    @property
    def all_schema_outputs(self) -> Tuple[SchemaOutput]:
        return tuple(inp_j for schema_i in self.schemas for inp_j in schema_i.outputs)

    @property
    def all_schema_input_types(self):
        """Get the set of all schema input types (over all specified schemas)."""
        return {inp_j for schema_i in self.schemas for inp_j in schema_i.input_types}

    @property
    def all_schema_output_types(self):
        """Get the set of all schema output types (over all specified schemas)."""
        return {out_j for schema_i in self.schemas for out_j in schema_i.output_types}

    @property
    def universal_input_types(self):
        """Get input types that are associated with all schemas"""

    @property
    def non_universal_input_types(self):
        """Get input types for each schema that are non-universal."""

    @property
    def defined_input_types(self):
        return self._defined_input_types

    @property
    def undefined_input_types(self):
        return self.all_schema_input_types - self.defined_input_types

    @property
    def undefined_inputs(self):
        return [
            inp_j
            for schema_i in self.schemas
            for inp_j in schema_i.inputs
            if inp_j.typ in self.undefined_input_types
        ]

    @property
    def unsourced_inputs(self):
        """Get schema input types for which no input sources are currently specified."""
        return self.all_schema_input_types - set(self.input_sources.keys())

    @property
    def provides_parameters(self):
        return tuple(j for schema in self.schemas for j in schema.provides_parameters)

    def get_sub_parameter_input_values(self):
        return [i for i in self.inputs if i.is_sub_value]

    def get_non_sub_parameter_input_values(self):
        return [i for i in self.inputs if not i.is_sub_value]

    def add_group(
        self, name: str, where: ElementFilter, group_by_distinct: ParameterPath
    ):
        group = ElementGroup(name=name, where=where, group_by_distinct=group_by_distinct)
        self.groups.add_object(group)

    def get_input_multiplicities(self, missing_multiplicities=None):
        """Get multiplicities for all inputs."""

        if self.undefined_input_types:
            missing_inputs = self.undefined_input_types - set(
                (missing_multiplicities or {}).keys()
            )
            if missing_inputs:
                raise MissingInputs(
                    f"The following inputs are not assigned values, so task input "
                    f"multiplicities cannot be resolved: {list(missing_inputs)!r}."
                )

        input_multiplicities = []
        for i in self.input_values:
            if i.sequences:
                for seq in i.sequences:
                    address = tuple([i.parameter.typ] + seq.address)
                    address_str = ".".join(address)
                    input_multiplicities.append(
                        {
                            "address": address,
                            "multiplicity": len(seq.values),
                            "nesting_order": self.nesting_order[address_str],
                        }
                    )
            else:
                input_multiplicities.append(
                    {
                        "address": (i.parameter.typ,),
                        "multiplicity": 1,
                        "nesting_order": -1,
                    }
                )

        for inp_type, multi in (missing_multiplicities or {}).items():
            input_multiplicities.append(
                {
                    "address": (inp_type,),
                    "multiplicity": multi,
                    "nesting_order": self.nesting_order[inp_type],
                }
            )

        return input_multiplicities


class WorkflowTask:
    """Class to represent a Task that is bound to a Workflow."""

    def __init__(
        self,
        template: Task,
        element_indices: List,
        index: int,
        workflow: Workflow,
    ):

        self._template = template
        self._element_indices = element_indices
        self._workflow = workflow
        self._index = index

    @property
    def template(self):
        return self._template

    @property
    def element_indices(self):
        return self._element_indices

    @property
    def elements(self):
        return [self.workflow.elements[i] for i in self.element_indices]

    @property
    def workflow(self):
        return self._workflow

    @property
    def num_elements(self):
        return len(self.element_indices)

    @property
    def index(self):
        """Zero-based position within the workflow. Uses initial index if appending to the
        workflow is not complete."""
        return self._index

    @property
    def name(self):
        return self.template.name

    @property
    def insert_ID(self):
        return self.template.insert_ID

    @property
    def dir_name(self):
        return self.template.dir_name

    @property
    def unique_name(self):
        return self.workflow.get_task_unique_names()[self.index]