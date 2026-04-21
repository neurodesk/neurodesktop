# Neurodesk Agent Context

## Critical Rules

1. You need to module load neuroimaging tools, e.g.:

```python
#load FSL 6.0.4
import module
await module.load('fsl/6.0.4')
await module.list()
```

Then you can execute commands using:

```python
!fslmaths
```

You can find out which tools exist via:

```python
# Check available modules  
available = await module.avail()  
print(available)  
```

1. Always make sure you visualize the steps you are doing:

```python
from ipyniivue import NiiVue
nv = NiiVue()
nv.load_volumes([{"url": "file.nii.gz"}])
nv
```

## Workflow Standards

### A. Tool Selection

* **Trade-off Analysis:** Neuroimaging often offers multiple tools for one task (e.g., FSL vs. ANTs for registration).
  * *Rule:* Before writing code, list the available options, explain the trade-offs (speed, accuracy, input requirements, licensing) to the user, and ask for a decision.
  * *Preference:* Prioritize tools available via `module load` over custom installations unless necessary.

## 4. Critical Constraints

* **DO NOT** assume a module is loaded; always load it explicitly in the script.
* **DO NOT** hardcode absolute paths specific to temporary sessions; use relative paths or defined variables.
