---
title: Neurodesktop domain language
description: Terms used for image release validation
status: current
last-reviewed: "2026-10-03"
---

# Neurodesktop

These terms describe the images and environments used for release validation.

## Language

**Candidate image**:
An architecture-specific Neurodesktop image awaiting release validation and
promotion. Its identity belongs to one build run and attempt.

**Runtime profile**:
A specified execution environment for validating a candidate image, including
its startup privileges and CVMFS availability. HPC simulation is the profile
that represents a foreign, unprivileged user in an Apptainer allocation.

**Image runtime validation**:
The lifecycle of checking a candidate image under one runtime profile,
including startup, runtime checks, and release of its temporary resources.
