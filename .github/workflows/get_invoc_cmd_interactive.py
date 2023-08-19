"""Script to output the invocation command within an interactive python or ipython shell.

This is used in the test GHA workflow to check that we get the expected result. This only
works on POSIX operating systems.

"""

import sys
import pexpect

executable = sys.argv[1]
prompt = {
    "python": ">>>",
    "ipython": "In.*",
}[executable]

c = pexpect.spawnu(executable)
c.expect(prompt)

if executable == "ipython":
    # turn off pretty printing so parsing expected output is easier:
    c.sendline("%pprint")

c.sendline("import hpcflow.app as hf")
c.sendline("hf.run_time_info.invocation_command")
c.expect("\('.*'\)")
c.kill(1)
print(c.after)
