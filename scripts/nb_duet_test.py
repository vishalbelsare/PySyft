# stdlib
from collections import defaultdict
import os
from pathlib import Path
import re
import shutil

# third party
from nbconvert import PythonExporter
from nbconvert.writers import FilesWriter
import nbformat

tests = defaultdict(list)
output_dir = Path("tests/syft/notebooks")
checkpoint_dir = Path("tests/syft/notebooks/checkpoints")

try:
    os.mkdir(output_dir)
except BaseException as e:
    print("os.mkdir failed ", e)

try:
    shutil.rmtree(checkpoint_dir)
    os.mkdir(checkpoint_dir)
except BaseException as e:
    print("os.mkdir failed ", e)


for path in Path("examples/homomorphic-encryption").rglob("*.ipynb"):
    if ".ipynb_checkpoints" in str(path):
        continue

    testname = re.sub("[^0-9a-zA-Z]+", "_", str(path))
    output = output_dir / testname

    file_name = str(path.stem)
    is_do = False
    is_ds = False

    if file_name.endswith("_Data_Scientist"):
        testcase = file_name.replace("_Data_Scientist", "")
        tests[testcase].append(testname)
        is_ds = True
    elif file_name.endswith("_Data_Owner"):
        testcase = file_name.replace("_Data_Owner", "")
        tests[testcase].append(testname)
        is_do = True
    else:
        continue

    notebook_nodes = nbformat.read(path, as_version=4)

    custom_cell = nbformat.v4.new_code_cell(
        source="""
import os
import time
import asyncio
from pathlib import Path

loop = asyncio.get_event_loop()
"""
    )
    notebook_nodes["cells"].insert(0, custom_cell)

    for idx, cell in enumerate(notebook_nodes["cells"]):
        if cell["cell_type"] == "markdown" and "Checkpoint" in cell["source"]:
            checkpoint = (
                cell["source"]
                .lower()
                .split("checkpoint")[1]
                .strip()
                .split(":")[0]
                .strip()
            )

            # For DO, we wait until DS gets to the same checkpoint
            if is_do:
                ck_file = "checkpoints/" + (
                    testcase + "_DO_checkpoint_" + str(checkpoint)
                )
                wait_file = "checkpoints/" + (
                    testcase + "_DS_checkpoint_" + str(checkpoint)
                )
                checkpoint_cell = nbformat.v4.new_code_cell(
                    source=f"""
Path(\"{ck_file}\").touch()
for retry in range(20):
    if Path(\"{wait_file}\").exists():
        break
    task = loop.create_task(asyncio.sleep(0.1))
    loop.run_until_complete(task)
                                                            """
                )

            # For DS, we wait until DO gets to the next checkpoint
            elif is_ds:
                ck_file = "checkpoints/" + (
                    testcase + "_DS_checkpoint_" + str(checkpoint)
                )
                wait_file = "checkpoints/" + (
                    testcase + "_DO_checkpoint_" + str(int(checkpoint) + 1)
                )
                checkpoint_cell = nbformat.v4.new_code_cell(
                    source=f"""
Path(\"{ck_file}\").touch()
for retry in range(20):
    if Path(\"{wait_file}\").exists():
        break
    task = loop.create_task(asyncio.sleep(0.1))
    loop.run_until_complete(task)
assert Path(\"{wait_file}\").exists()
                                                            """
                )
            notebook_nodes["cells"][idx] = checkpoint_cell

    try:
        exporter = PythonExporter()

        (body, resources) = exporter.from_notebook_node(notebook_nodes)
        write_file = FilesWriter()
        write_file.write(output=body, resources=resources, notebook_name=str(output))
    except Exception as e:
        print(f"There was a problem exporting the file(s): {e}")

for case in tests:
    test = tests[case]
    if len(test) != 2:
        print("invalid testcase ", test)

    print(case, test)
