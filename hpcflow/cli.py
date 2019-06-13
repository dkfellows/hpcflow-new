"""`hpcflow.cli.py`

Module that exposes a command line interface for `hpcflow`.

"""

from pathlib import Path
from pprint import pprint

import click

from hpcflow import __version__
from hpcflow import api


def validate_task_ranges(ctx, param, value):
    """Validate the task range.

    Parameters
    ----------
    ctx
    param
    value : str
        Stringified comma-separated list, where each element indicates the
        tasks to submit for that channel of the Workflow. List elements can be
        one of:
            all
                submit all tasks in the given channel.
            n[-m[:s]]
                submit a range of tasks from task `n` to task `m`
                (inclusively), with an optional step size of `s`.
            <empty>
                submit no tasks from the given channel.

    Returns
    -------
    task_ranges : list of tuple
        (start, stop, step)

    """

    if value is None:
        return

    if ',' in value:
        value = value.split(',')
    else:
        value = [value]

    task_ranges = []
    for i in value:

        if i.strip() == 'all':
            task_ranges.append([1, -1, 1])
            continue

        elif i.strip() == '':
            task_ranges.append([])
            continue

        task_step = 1

        msg = ('Could not understand task range. It should be specified in '
               'the format: `n[-m[:s]]` where `n` is the starting task ID, `m` is '
               ' the ending task ID, and `s` is the task step size.')

        if '-' in i:
            # Task range
            task_start, task_stop = i.split('-')

            if ':' in task_stop:
                # With step size:
                task_stop, task_step = task_stop.split(':')

                try:
                    task_step = int(task_step)
                except ValueError:
                    raise click.BadParameter(msg)

            try:
                task_start = int(task_start)
                task_stop = int(task_stop)
            except ValueError:
                raise click.BadParameter(msg)

        else:
            # Single task
            try:
                task = int(i)
                task_start = task
                task_stop = task
            except ValueError:
                raise click.BadParameter(msg)

        if task_start > task_stop:
            msg = ('Task starting ID must be smaller than or equal to '
                   'task ending ID.')
            raise click.BadParameter(msg)

        task_range = [task_start, task_stop, task_step]
        task_ranges.append(task_range)

    return task_ranges


@click.group()
@click.version_option(version=__version__)
def cli():
    pass


@cli.command()
@click.option('--yes', '-y', is_flag=True)
def clean(directory=None, yes=True):
    """Clean the directory of all content generated by `hpcflow`."""
    msg = ('Do you want to remove all `hpc-flow`-generated files '
           'from {}?')
    if directory:
        msg = msg.format(directory)
    else:
        msg = msg.format('the current directory')
    if yes or click.confirm(msg):
        api.clean(dir_path=directory)


@cli.command()
@click.option('--directory', '-d')
@click.option('--json-file')
@click.option('--json')
@click.argument('profiles', nargs=-1, type=click.Path(exists=True))
def make(directory=None, profiles=None, json_file=None, json=None):
    """Generate a new Workflow."""
    print('hpcflow.cli.make')

    workflow_id = api.make_workflow(
        dir_path=directory,
        profile_list=profiles,
        json_file=json_file,
        json_str=json,
        clean=False,
    )
    print('Generated new Workflow with ID {}'.format(workflow_id))


@cli.command()
@click.option('--directory', '-d')
@click.option('--task', '-t', type=click.INT)
@click.argument('cmd_group_sub_id', type=click.INT)
def write_cmd(cmd_group_sub_id, task=None, directory=None):
    print('hpcflow.cli.write_cmd')
    api.write_cmd(
        cmd_group_sub_id,
        task,
        directory,
    )


@cli.command()
@click.option('--directory', '-d')
@click.option('--task', '-t', type=click.INT)
@click.argument('cmd_group_sub_id', type=click.INT)
def archive(cmd_group_sub_id, task, directory=None):
    print('hpcflow.cli.archive')
    api.archive(
        cmd_group_sub_id,
        task,
        directory
    )


@cli.command()
def stat():
    """Show the status of running tasks and the number completed tasks."""
    print('hpcflow.cli.stat')


@cli.command()
@click.option('--directory', '-d')
@click.option('--workflow-id', '-w')
@click.option('--json-file')
@click.option('--json')
@click.option('--task-ranges', '-t',
              help=('Task ranges are specified as a comma-separated list whose'
                    ' elements are one of: "n[-m[:s]]", "all" or "" (empty)'),
              callback=validate_task_ranges)
@click.argument('profiles', nargs=-1, type=click.Path(exists=True))
def submit(directory=None, workflow_id=None, task_ranges=None, profiles=None,
           json_file=None, json=None):
    """Submit(and optionally generate) a Workflow."""

    print('hpcflow.cli.submit')
    # print('task_ranges')
    # pprint(task_ranges)

    existing_ids = api.get_workflow_ids(directory)
    submit_args = {
        'dir_path': directory,
        'task_ranges': task_ranges,
    }

    if workflow_id:
        # Submit an existing Workflow.

        if not existing_ids:
            msg = 'There are no existing Workflows in the directory {}'
            raise ValueError(msg.format(directory))

        submit_args['workflow_id'] = workflow_id

        if workflow_id not in existing_ids:
            msg = ('The Workflow ID "{}" does not match an existing Workflow '
                   'in the directory {}. Existing Workflow IDs are {}')
            raise ValueError(msg.format(workflow_id, directory, existing_ids))

        submission_id = api.submit_workflow(**submit_args)

    else:
        # First generate a Workflow, and then submit it.

        make_workflow = True
        if existing_ids:
            # Check user did not want to submit existing Workflow:
            msg = 'Previous workflows exist with IDs: {}. Add new workflow?'
            make_workflow = click.confirm(msg.format(existing_ids))

            # TODO: if `make_workflow=False`, show existing IDs and offer to
            # submit one?

        if make_workflow:
            workflow_id = api.make_workflow(
                dir_path=directory,
                profile_list=profiles,
                json_file=json_file,
                json_str=json,
            )
            print('Generated new Workflow with ID {}'.format(workflow_id))

            submit_args['workflow_id'] = workflow_id
            submission_id = api.submit_workflow(**submit_args)

        else:
            print('Exiting.')
            return

    print('Submitted Workflow (ID {}) with submission '
          'ID {}'.format(workflow_id, submission_id))


if __name__ == '__main__':
    cli()
