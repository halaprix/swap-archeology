"""Build a Multicall3 aggregate3 arg string from probe specs.
Each spec: {"label":..., "addr":..., "sig":..., "args":[...]}  (args optional)
"""

import json
import subprocess
import sys


def calldata(sig, args):
    return subprocess.run(
        ["cast", "calldata", sig, *[str(a) for a in args]],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def build(trips):
    return (
        "["
        + ",".join(f"({t['addr']},true,{calldata(t['sig'], t.get('args', []))})" for t in trips)
        + "]"
    )


if __name__ == "__main__":
    with open(sys.argv[1]) as input_file:
        print(build(json.load(input_file)))
