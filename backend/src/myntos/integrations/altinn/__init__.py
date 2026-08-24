"""Altinn integration.

Submissions to Skatteetaten flow through Altinn's instance APIs, and a Digdir
token is never the credential those APIs accept — it is exchanged for an Altinn
token first. Two modules, matching the two halves of that:

- :mod:`.tokens` — the exchange, and the distinction that matters: a machine
  (Maskinporten) token may fetch, create an instance and upload; only a person's
  (ID-porten) token may submit an MVA-melding or a skattemelding.
- :mod:`.instances` — the app instance API: create, upload, ``process/next``
  twice, read the receipt.

Delegation — acting on behalf of clients, the regnskapsfører scenario — rides on
the ``consumer_org`` claim in the Maskinporten grant, which is why it appears
here as an argument rather than as configuration. The Altinn 3 systembruker
model that replaces it lands in the skattemelding milestone (docs/ROADMAP.md).
"""

from myntos.integrations.altinn.instances import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    AltinnInstanceError,
    AppInstanceClient,
    DataElement,
    Instance,
    InstanceRef,
)
from myntos.integrations.altinn.tokens import (
    INSTANCE_SCOPES,
    AltinnAuthError,
    AltinnError,
    AltinnToken,
    MachineTokenSource,
    exchange_id_porten_token,
    exchange_token,
    machine_tokens,
    reset_machine_tokens,
)

__all__ = [
    "INSTANCE_SCOPES",
    "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "AltinnAuthError",
    "AltinnError",
    "AltinnInstanceError",
    "AltinnToken",
    "AppInstanceClient",
    "DataElement",
    "Instance",
    "InstanceRef",
    "MachineTokenSource",
    "exchange_id_porten_token",
    "exchange_token",
    "machine_tokens",
    "reset_machine_tokens",
]
