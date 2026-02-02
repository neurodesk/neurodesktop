# Neurodesk Context
You are working in the neurodesk environment that uses Lmod Modules to load Neuroimaging software onto the path
- NEVER run neuroimaging tools directly: ALWAYS write a bash script that uses "module load" and then use the right tools in there with the correct and explicit version
- ALWAYS name this file analysis_step_summary.sh, but replace step with the actual step number and summary with a short descriptive word of the analysis performed.
- software tools can be tools available through Neurodesk or you can write your own python scripts. You have a full miniconda environment available and can use mamba/conda/pip for installing packages 
- for analyses with multiple steps prefer a nipype pipeline implementation 
- you can find more information via running "module help <module name>"
- Once the analysis script is written, ask the user if you should execute the script for the user.
