from pathlib import Path
import subprocess
from typing import List, Tuple
from hpcflow.sdk.submission.schedulers import Scheduler
from hpcflow.sdk.submission.shells.base import Shell


class SGEPosix(Scheduler):
    """

    Notes
    -----
    - runs in serial by default

    References
    ----------
    [1] https://gridscheduler.sourceforge.net/htmlman/htmlman1/qsub.html

    """

    DEFAULT_SHEBANG_ARGS = ""
    DEFAULT_SUBMIT_CMD = "qsub"
    DEFAULT_SHOW_CMD = "qstat"
    DEFAULT_DEL_CMD = "qdel"
    DEFAULT_JS_CMD = "#$"
    DEFAULT_ARRAY_SWITCH = "-t"
    DEFAULT_ARRAY_ITEM_VAR = "SGE_TASK_ID"
    DEFAULT_CWD_SWITCH = "-cwd"

    def __init__(self, cwd_switch=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cwd_switch = cwd_switch or self.DEFAULT_CWD_SWITCH

    def format_core_request_lines(self, num_cores, parallel_env):
        lns = []
        if num_cores > 1:
            lns.append(f"{self.js_cmd} -pe {parallel_env} {num_cores}")
        return lns

    def format_array_request(self, num_elements):
        return f"{self.js_cmd} {self.array_switch} 1-{num_elements}"

    def format_std_stream_file_option_lines(self, is_array, sub_idx):

        # note: we can't modify the file names
        base = f"./artifacts/submissions/{sub_idx}"
        return [
            f"{self.js_cmd} -o {base}",
            f"{self.js_cmd} -e {base}",
        ]

    def format_options(self, resources, num_elements, is_array, sub_idx):

        # TODO: I think the PEs are set by the sysadmins so they should be set in the
        # config file as a mapping between num_cores/nodes and PE names?
        # `qconf -spl` shows a list of PEs

        opts = []
        opts.append(self.format_switch(self.cwd_switch))
        opts.extend(self.format_core_request_lines(resources.num_cores, "smp.pe"))
        if is_array:
            opts.append(self.format_array_request(num_elements))

        opts.extend(self.format_std_stream_file_option_lines(is_array, sub_idx))
        return "\n".join(opts)

    def get_version_info(self):
        vers_cmd = [self.show_cmd, "-help"]
        proc = subprocess.run(
            args=vers_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout = proc.stdout.decode().strip()
        version_str = stdout.split("\n")[0].strip()
        name, version = version_str.split()
        out = {
            "scheduler_name": name,
            "scheduler_version": version,
        }
        return out

    def get_submit_command(
        self,
        shell: Shell,
        js_path: str,
        deps: List[Tuple],
    ) -> List[str]:

        cmd = [self.submit_cmd, "-terse"]

        dep_job_IDs = []
        dep_job_IDs_arr = []
        for job_ID, is_array_dep in deps:
            if is_array_dep:  # array dependency
                dep_job_IDs_arr.append(str(job_ID))
            else:
                dep_job_IDs.append(str(job_ID))

        if dep_job_IDs:
            cmd.append("-hold_jid")
            cmd.append(",".join(dep_job_IDs))

        if dep_job_IDs_arr:
            cmd.append("-hold_jid_ad")
            cmd.append(",".join(dep_job_IDs_arr))

        cmd.append(js_path)
        return cmd

    def parse_submission_output(self, stdout: str) -> str:
        """Extract scheduler reference for a newly submitted jobscript"""
        job_ID = stdout  # since we submit with "-terse"
        return job_ID