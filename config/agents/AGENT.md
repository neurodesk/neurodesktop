# Neurodesk Context
You are working in the neurodesk environment that uses Lmod Modules to load Neuroimaging software onto the PATH
- software tools can be tools available through Neurodesk or you can write your own python scripts. You have a full miniconda environment available and can use mamba/conda/pip for installing packages. Document installations of packages in the bash script as well!
- you can find more information and examples about neuroimaging tools via running "module help <module name>"
- Often there are multiple neuroimaging tools that can do the job. If there are multiple options for tools ask the user which tool to use and explain the trade-offs between tools!
- you can also work in a jupyter notebook, then you need to load modules via this python code: "import module; await module.load('toolname/version')"
- NEVER run neuroimaging tools directly: ALWAYS write a bash script that uses "module load" to load the correct tool with explicit version set
- ALWAYS name the analysis file analysis_step_summary.sh - replace step with the actual step number and summary with a descriptive word of the analysis performed.
- Once the analysis script is written, submit the script to SLURM - these processings will take a long time! Then check the progress via inspecting the que and the logfiles.
- Once results are computed, check results for plausibility. If possible create a PNG of the result and look at the result. Did the analysis work?
- when downloading sample data from openneuro make sure to use datalad and store the data in the current directory and the code used for downloading the data is in a script file for full reproducibility
