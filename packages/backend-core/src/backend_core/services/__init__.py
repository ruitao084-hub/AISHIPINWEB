"""Domain services — the only layer permitted to change business state.

Controllers translate HTTP; providers talk to vendors; services decide. This
is the boundary §20 protects when it forbids a provider adapter from touching
project status or credits.

Populated from PHASE 3.
"""
