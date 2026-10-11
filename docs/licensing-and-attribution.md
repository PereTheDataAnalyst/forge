# Licensing and attribution

This page is a plain summary for engineers and is not legal advice.

## N-ATLAS terms

N-ATLAS is distributed under the **N-ATLaS Terms of Use** (the model card calls it an Open-Source Research and Innovation License). Read the full text on the [model page](https://huggingface.co/NCAIR1/N-ATLaS) before deploying. As read from the model card:

- Public use must display this attribution: *N-ATLaS is an initiative of the Federal Ministry of Communications, Innovation and Digital Economy, and powered by Awarri Technologies.*
- Derivative works must be released under the same terms, and the terms contain further conditions for renamed derivatives.
- The terms set a limit of **1,000 active end users** before a commercial licence is needed, and say the model is not an enterprise-grade or commercial system.
- The terms list prohibited uses.

The speech models carry their own attribution text on their model cards. Check each card before showing a speech model publicly.

## What this means for Forge

- Forge shows the attribution in the website footer, in the README and in `/api/v1/models` responses.
- A hosted gateway that serves many developers can exceed the 1,000 active end user limit even if each developer stays below it. Confirm the position with the licensor, in writing, before operating beyond the competition beta. `REGISTRATION_ENABLED=false` closes sign-ups.
- Forge cannot see the end users of the applications that its customers build, so it cannot enforce the limit on their behalf.
- The licence of this repository is separate from the model terms. No `LICENSE` file has been added yet, which is a decision for StackTechnology. Until one is added the default is that all rights are reserved.

## Endorsement

N-ATLAS Forge is an independent project by StackTechnology. It is not an official product of, and is not endorsed by, the Federal Ministry of Communications, Innovation and Digital Economy, NCAIR, NITDA or Awarri Technologies. Do not show their logos or imply a partnership without written permission.

## Third-party software

Dependencies keep their own licences. Fonts (IBM Plex Sans, IBM Plex Mono, Saira) are bundled through the `@fontsource` packages under the SIL Open Font License 1.1.
