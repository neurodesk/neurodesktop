# Neurodesk Context
You are working in the neurodesk environment that uses Lmod Modules to load Neuroimaging software onto the path
- NEVER run neuroimaging tools directly: ALWAYS write a bash script that uses "module load" and then use the right tools in there with the correct and explicit version
- ALWAYS name this file analysis_step_summary.sh, but replace step with the actual step number and summary with a short descriptive word of the analysis performed.
- software tools can be tools available through Neurodesk or you can write your own python scripts. You have a full miniconda environment available and can use mamba/conda/pip for installing packages. Document installations of packages in the bash script as well!
- for analyses with multiple steps prefer a nipype pipeline implementation 
- you can find more information and examples about neuroimaging tools via running "module help <module name>"
- Once the analysis script is written, execute the script in the background - these processings will take a long time!
- ALWAYS check results for plausability
- when downloading sample data make sure the data is stored in the current directory and the code used for downloading the data is in a script file for full reproducibility
