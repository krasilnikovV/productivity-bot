import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from aiogram import Bot, Dispatcher
from aiogram.methods import TelegramMethod
from aiogram.types import Update
from pydantic import ValidationError

from productivity_bot.application.ports import (
    ClaimedUpdate,
    TaskMutationConfirmedError,
    TaskMutationNotAppliedError,
    TaskMutationOutcomeUnknownError,
    TaskReadError,
    TelegramUpdateInboxRepository,
    UpdateTransitionError,
)

logger = logging.getLogger(__name__)

_MAX_ERROR_LENGTH = 1_000


class MutationMarkerPersistenceError(Exception):
    """Raised when an attempt cannot durably enter its mutation phase."""


class TelegramUpdateProcessingAttempt:
    """Expose the durable mutation boundary for one claimed update."""

    def __init__(
        self,
        claimed_update: ClaimedUpdate,
        update_inbox_repository: TelegramUpdateInboxRepository,
    ) -> None:
        self._claimed_update = claimed_update
        self._update_inbox_repository = update_inbox_repository
        self._external_mutation_started = False

    @property
    def external_mutation_started(self) -> bool:
        return self._external_mutation_started

    async def mark_external_mutation_started(self) -> None:
        if self._external_mutation_started:
            return

        try:
            await self._update_inbox_repository.mark_external_mutation_started(
                self._claimed_update.update_id,
                self._claimed_update.attempt_count,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise MutationMarkerPersistenceError(
                "Failed to record external mutation marker"
            ) from error
        self._external_mutation_started = True


class TelegramUpdateWorker:
    """Process durably stored Telegram updates with bounded concurrency."""

    def __init__(
        self,
        *,
        bot: Bot,
        dispatcher: Dispatcher,
        update_inbox_repository: TelegramUpdateInboxRepository,
        concurrency: int,
        poll_interval: float,
        claim_timeout: timedelta,
        recovery_interval: float,
        shutdown_grace_period: float,
    ) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._update_inbox_repository = update_inbox_repository
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._claim_timeout = claim_timeout
        self._recovery_interval = recovery_interval
        self._shutdown_grace_period = shutdown_grace_period
        self._stop_event = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._tasks:
            return

        self._stop_event.clear()
        await self._recover_abandoned_updates()
        self._tasks = {
            asyncio.create_task(
                self._processing_loop(),
                name=f"telegram-update-processor-{worker_number}",
            )
            for worker_number in range(self._concurrency)
        }
        self._tasks.add(
            asyncio.create_task(
                self._recovery_loop(),
                name="telegram-update-recovery",
            )
        )

    async def stop(self) -> None:
        if not self._tasks:
            return

        tasks = tuple(self._tasks)
        self._stop_event.set()
        _, pending = await asyncio.wait(
            tasks,
            timeout=self._shutdown_grace_period,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def wait(self) -> None:
        if not self._tasks:
            raise RuntimeError("Telegram update worker is not running")

        done, _ = await asyncio.wait(
            tuple(self._tasks),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._stop_event.is_set():
            return

        completed_task = next(iter(done))
        if completed_task.cancelled():
            raise RuntimeError(
                f"Telegram update worker task {completed_task.get_name()} "
                "was cancelled unexpectedly"
            )
        error = completed_task.exception()
        if error is not None:
            raise RuntimeError(
                f"Telegram update worker task {completed_task.get_name()} "
                "failed unexpectedly"
            ) from error
        raise RuntimeError(
            f"Telegram update worker task {completed_task.get_name()} "
            "stopped unexpectedly"
        )

    async def _processing_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                claimed_update = (
                    await self._update_inbox_repository.claim_pending_update()
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to claim a pending Telegram update")
                await self._wait_or_stop(self._poll_interval)
                continue

            if claimed_update is None:
                await self._wait_or_stop(self._poll_interval)
                continue

            if self._stop_event.is_set():
                await self._reschedule_safe_attempt(
                    claimed_update,
                    "Worker shutdown started after the update was claimed",
                )
                continue

            try:
                await self._process_update(claimed_update)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to persist the processing outcome for Telegram update %s",
                    claimed_update.update_id,
                )

    async def _process_update(self, claimed_update: ClaimedUpdate) -> None:
        try:
            update = Update.model_validate(
                claimed_update.payload,
                context={"bot": self._bot},
            )
        except ValidationError as error:
            await self._persist_processing_transition(
                claimed_update,
                "mark failed",
                partial(
                    self._update_inbox_repository.mark_failed,
                    claimed_update.update_id,
                    claimed_update.attempt_count,
                    _validation_error_message(error),
                ),
            )
            return
        processing_attempt = TelegramUpdateProcessingAttempt(
            claimed_update,
            self._update_inbox_repository,
        )
        try:
            result = await self._dispatcher.feed_update(
                self._bot,
                update,
                processing_attempt=processing_attempt,
            )
        except asyncio.CancelledError:
            raise
        except MutationMarkerPersistenceError as error:
            detail_error = (
                error.__cause__
                if isinstance(error.__cause__, Exception)
                else error
            )
            await self._reschedule_safe_attempt(
                claimed_update,
                _error_message(str(error), detail_error),
            )
            return
        except TaskReadError as error:
            if error.retryable:
                await self._reschedule_safe_attempt(
                    claimed_update,
                    _error_message("Task read failed", error),
                )
            else:
                await self._persist_processing_transition(
                    claimed_update,
                    "mark failed",
                    partial(
                        self._update_inbox_repository.mark_failed,
                        claimed_update.update_id,
                        claimed_update.attempt_count,
                        _error_message("Task read failed", error),
                    ),
                )
            return
        except TaskMutationNotAppliedError as error:
            if error.retryable:
                await self._reschedule_safe_attempt(
                    claimed_update,
                    _error_message("Task mutation was not applied", error),
                )
            else:
                await self._persist_processing_transition(
                    claimed_update,
                    "mark failed",
                    partial(
                        self._update_inbox_repository.mark_failed,
                        claimed_update.update_id,
                        claimed_update.attempt_count,
                        _error_message("Task mutation was rejected", error),
                    ),
                )
            return
        except TaskMutationConfirmedError as error:
            logger.error(
                "Telegram update %s completed its mutation but could not consume "
                "the result: %s",
                claimed_update.update_id,
                error,
            )
            await self._persist_processing_transition(
                claimed_update,
                "mark succeeded",
                partial(
                    self._update_inbox_repository.mark_succeeded,
                    claimed_update.update_id,
                    claimed_update.attempt_count,
                ),
            )
            return
        except TaskMutationOutcomeUnknownError as error:
            await self._mark_uncertain(claimed_update, error)
            return
        except Exception as error:
            if processing_attempt.external_mutation_started:
                await self._mark_uncertain(claimed_update, error)
            else:
                logger.exception(
                    "Telegram update %s failed before an external mutation",
                    claimed_update.update_id,
                )
                await self._persist_processing_transition(
                    claimed_update,
                    "mark failed",
                    partial(
                        self._update_inbox_repository.mark_failed,
                        claimed_update.update_id,
                        claimed_update.attempt_count,
                        _error_message("Processing failed before mutation", error),
                    ),
                )
            return

        await self._persist_processing_transition(
            claimed_update,
            "mark succeeded",
            partial(
                self._update_inbox_repository.mark_succeeded,
                claimed_update.update_id,
                claimed_update.attempt_count,
            ),
        )
        if isinstance(result, TelegramMethod):
            try:
                await self._bot(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to send Telegram reply for succeeded update %s",
                    claimed_update.update_id,
                )

    async def _mark_uncertain(
        self,
        claimed_update: ClaimedUpdate,
        error: Exception,
    ) -> None:
        logger.error(
            "Telegram update %s has an unknown external mutation outcome",
            claimed_update.update_id,
            exc_info=error,
        )
        await self._persist_processing_transition(
            claimed_update,
            "mark uncertain",
            partial(
                self._update_inbox_repository.mark_uncertain,
                claimed_update.update_id,
                claimed_update.attempt_count,
                _error_message("External mutation outcome is unknown", error),
            ),
        )

    async def _reschedule_safe_attempt(
        self,
        claimed_update: ClaimedUpdate,
        error: str,
    ) -> None:
        available_at = datetime.now(UTC) + timedelta(seconds=self._poll_interval)
        await self._persist_processing_transition(
            claimed_update,
            "reschedule",
            partial(
                self._update_inbox_repository.reschedule,
                claimed_update.update_id,
                claimed_update.attempt_count,
                error,
                available_at,
            ),
        )

    async def _persist_processing_transition(
        self,
        claimed_update: ClaimedUpdate,
        transition_name: str,
        transition: Callable[[], Awaitable[None]],
    ) -> None:
        while True:
            try:
                await transition()
                return
            except asyncio.CancelledError:
                raise
            except UpdateTransitionError:
                raise
            except Exception:
                logger.exception(
                    "Failed to %s for Telegram update %s; retrying",
                    transition_name,
                    claimed_update.update_id,
                )
                await asyncio.sleep(self._poll_interval)

    async def _recovery_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._wait_or_stop(self._recovery_interval)
            if self._stop_event.is_set():
                return
            try:
                await self._recover_abandoned_updates()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to recover abandoned Telegram updates")

    async def _recover_abandoned_updates(self) -> None:
        claimed_before = datetime.now(UTC) - self._claim_timeout
        recovered = await self._update_inbox_repository.recover_abandoned_updates(
            claimed_before
        )
        if recovered.retried_count or recovered.uncertain_count:
            logger.info(
                "Recovered abandoned Telegram updates: %s retried, %s uncertain",
                recovered.retried_count,
                recovered.uncertain_count,
            )

    async def _wait_or_stop(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except TimeoutError:
            pass


def _validation_error_message(error: ValidationError) -> str:
    details = []
    for item in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in item["loc"])
        details.append(f"{item['type']} at {location or '<root>'}")
    message = "Invalid durable Telegram update payload: " + "; ".join(details)
    return message[:_MAX_ERROR_LENGTH]


def _error_message(context: str, error: Exception) -> str:
    message = f"{context}: {type(error).__name__}: {error}"
    return message[:_MAX_ERROR_LENGTH]
