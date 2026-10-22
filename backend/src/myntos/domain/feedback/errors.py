"""Errors raised by the feedback domain.

Their own hierarchy, mapped to status codes in :mod:`myntos.api.errors` like
the others. The messages are bokmål prose a page can show as it is, and an
agent can read: both kinds of sender meet them.
"""


class FeedbackError(Exception):
    """Base class for every refused piece of feedback."""


class InvalidFeedbackError(FeedbackError):
    """The text is empty, too long, or a field is not what it says it is."""


class FeedbackNotFoundError(FeedbackError):
    """No feedback has this reference: never sent, withdrawn, or deleted after its time.

    One error for all three. The reference is the only thing that names an
    item, so there is nobody to tell apart.
    """


class FeedbackBusyError(FeedbackError):
    """Too much has arrived in the last hour. Not about the sender: nobody here is counted.

    The cap is on everything together, because the senders are anonymous and
    there is nobody to count separately (docs/feedback.md, the cap).
    """


class FeedbackInboxFullError(FeedbackBusyError):
    """Too much is unread, and nothing more is taken until the operator has read some.

    A subclass so that a caller catching the hour's cap catches this too; its
    own status says that waiting an hour will not help.
    """
