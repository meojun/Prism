G. Minimal E2E regression, run only after the 16 targeted tests passed.
   Two runs on the containment runtime 49f47ebd7c75aecd:
     1. T0  tau=0        steady r8 s3  window 60 cooldown 30  (reproduction condition)
     2. T1  tau=0.00035  steady r8 s3  window 60 cooldown 30  (historical normal reference)
   E2E PASS is used only as regression confirmation, never as fix evidence.
