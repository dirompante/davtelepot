"""Provide a simple Bot object, mirroring Telegram API methods.

camelCase methods mirror API directly, while snake_case ones act as middlewares
    someway.
"""

# Standard library modules
import asyncio
import logging

# Third party modules
from aiohttp import web

# Project modules
from api import TelegramBot, TelegramError
from utilities import get_secure_key

# Do not log aiohttp `INFO` and `DEBUG` levels
logging.getLogger('aiohttp').setLevel(logging.WARNING)


class Bot(TelegramBot):
    """Simple Bot object, providing methods corresponding to Telegram bot API.

    Multiple Bot() instances may be run together, along with a aiohttp web app.
    """

    bots = []
    runner = None
    local_host = 'localhost'
    port = 3000
    final_state = 0
    _maintenance_message = ("I am currently under maintenance!\n"
                            "Please retry later...")
    _authorization_denied_message = None

    def __init__(
        self, token, hostname='', certificate=None, max_connections=40,
        allowed_updates=[]
    ):
        """Init a bot instance.

        token : str
            Telegram bot API token.
        hostname : str
            Domain (or public IP address) for webhooks.
        certificate : str
            Path to domain certificate.
        max_connections : int (1 - 100)
            Maximum number of HTTPS connections allowed.
        allowed_updates : List(str)
            Allowed update types (empty list to allow all).
        """
        self.__class__.bots.append(self)
        super().__init__(token)
        self._offset = 0
        self._hostname = hostname
        self._certificate = certificate
        self._max_connections = max_connections
        self._allowed_updates = allowed_updates
        self._session_token = get_secure_key(length=10)
        self._name = None
        self._telegram_id = None
        # The following routing table associates each type of Telegram `update`
        #   with a Bot method to be invoked on it.
        self.routing_table = {
            'message': self.message_router,
            'edited_message': self.edited_message_handler,
            'channel_post': self.channel_post_handler,
            'edited_channel_post': self.edited_channel_post_handler,
            'inline_query': self.inline_query_handler,
            'chosen_inline_result': self.chosen_inline_result_handler,
            'callback_query': self.callback_query_handler,
            'shipping_query': self.shipping_query_handler,
            'pre_checkout_query': self.pre_checkout_query_handler,
            'poll': self.poll_handler,
        }
        self.message_handlers = {
            'text': self.text_message_handler,
            'audio': self.audio_message_handler,
            'document': self.document_message_handler,
            'animation': self.animation_message_handler,
            'game': self.game_message_handler,
            'photo': self.photo_message_handler,
            'sticker': self.sticker_message_handler,
            'video': self.video_message_handler,
            'voice': self.voice_message_handler,
            'video_note': self.video_note_message_handler,
            'contact': self.contact_message_handler,
            'location': self.location_message_handler,
            'venue': self.venue_message_handler,
            'poll': self.poll_message_handler,
            'new_chat_members': self.new_chat_members_message_handler,
            'left_chat_member': self.left_chat_member_message_handler,
            'new_chat_title': self.new_chat_title_message_handler,
            'new_chat_photo': self.new_chat_photo_message_handler,
            'delete_chat_photo': self.delete_chat_photo_message_handler,
            'group_chat_created': self.group_chat_created_message_handler,
            'supergroup_chat_created': (
                self.supergroup_chat_created_message_handler
            ),
            'channel_chat_created': self.channel_chat_created_message_handler,
            'migrate_to_chat_id': self.migrate_to_chat_id_message_handler,
            'migrate_from_chat_id': self.migrate_from_chat_id_message_handler,
            'pinned_message': self.pinned_message_message_handler,
            'invoice': self.invoice_message_handler,
            'successful_payment': self.successful_payment_message_handler,
            'connected_website': self.connected_website_message_handler,
            'passport_data': self.passport_data_message_handler
        }
        self.individual_text_message_handlers = dict()
        self._under_maintenance = False
        self._allowed_during_maintenance = []
        self._maintenance_message = None
        # Default chat_id getter: same chat as update
        self.get_chat_id = lambda update: (
            update['message']['chat']['id']
            if 'message' in update and 'chat' in update['message']
            else update['chat']['id']
            if 'chat' in update
            else None
        )
        # Message to be returned if user is not allowed to call method
        self._authorization_denied_message = None
        # Default authorization function (always return True)
        self.authorization_function = lambda update, authorization_level: True
        return

    @property
    def hostname(self):
        """Hostname for the webhook URL.

        It must be a public domain or IP address. Port may be specified.
        A custom webhook url, including bot token and a random token, will be
        generated for Telegram to post new updates.
        """
        return self._hostname

    @property
    def webhook_url(self):
        """URL where Telegram servers should post new updates.

        It must be a public domain name or IP address. Port may be specified.
        """
        if not self.hostname:
            return ''
        return (
            f"{self.hostname}/webhook/{self.token}_{self.session_token}/"
        )

    @property
    def webhook_local_address(self):
        """Local address where Telegram updates are routed by revers proxy."""
        return (
            f"/webhook/{self.token}_{self.session_token}/"
        )

    @property
    def certificate(self):
        """Public certificate for `webhook_url`.

        May be self-signed
        """
        return self._certificate

    @property
    def max_connections(self):
        """Maximum number of simultaneous HTTPS connections allowed.

        Telegram will open as many connections as possible to boost bot’s
            throughput, lower values limit the load on bot‘s server.
        """
        return self._max_connections

    @property
    def allowed_updates(self):
        """List of update types to be retrieved.

        Empty list to allow all updates.
        """
        return self._allowed_updates

    @property
    def name(self):
        """Bot name."""
        return self._name

    @property
    def telegram_id(self):
        """Telegram id of this bot."""
        return self._telegram_id

    @property
    def session_token(self):
        """Return a token generated with the current instantiation."""
        return self._session_token

    @property
    def offset(self):
        """Return last update id.

        Useful to ignore repeated updates and restore original update order.
        """
        return self._offset

    @property
    def under_maintenance(self):
        """Return True if bot is under maintenance.

        While under maintenance, bot will reply `self.maintenance_message` to
            any update, except those which `self.is_allowed_during_maintenance`
            returns True for.
        """
        return self._under_maintenance

    @property
    def allowed_during_maintenance(self):
        """Return the list of criteria to allow an update during maintenance.

        If any of this criteria returns True on an update, that update will be
            handled even during maintenance.
        """
        return self._allowed_during_maintenance

    @property
    def maintenance_message(self):
        """Message to be returned if bot is under maintenance.

        If instance message is not set, class message is returned.
        """
        if self._maintenance_message:
            return self._maintenance_message
        if self.__class__.maintenance_message:
            return self.__class__._maintenance_message
        return ("I am currently under maintenance!\n"
                "Please retry later...")

    @property
    def authorization_denied_message(self):
        """Return this text if user is unauthorized to make a request.

        If instance message is not set, class message is returned.
        """
        if self._authorization_denied_message:
            return self._authorization_denied_message
        return self.__class__._authorization_denied_message

    async def message_router(self, update):
        """Route Telegram `message` update to appropriate message handler."""
        for key, value in update.items():
            if key in self.message_handlers:
                return await self.message_handlers[key](update)
        logging.error(
            f"The following message update was received: {update}\n"
            "However, this message type is unknown."
        )

    async def edited_message_handler(self, update):
        """Handle Telegram `edited_message` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this edited_message handler does nothing yet."
        )
        return

    async def channel_post_handler(self, update):
        """Handle Telegram `channel_post` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this channel_post handler does nothing yet."
        )
        return

    async def edited_channel_post_handler(self, update):
        """Handle Telegram `edited_channel_post` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this edited_channel_post handler does nothing yet."
        )
        return

    async def inline_query_handler(self, update):
        """Handle Telegram `inline_query` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this inline_query handler does nothing yet."
        )
        return

    async def chosen_inline_result_handler(self, update):
        """Handle Telegram `chosen_inline_result` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this chosen_inline_result handler does nothing yet."
        )
        return

    async def callback_query_handler(self, update):
        """Handle Telegram `callback_query` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this callback_query handler does nothing yet."
        )
        return

    async def shipping_query_handler(self, update):
        """Handle Telegram `shipping_query` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this shipping_query handler does nothing yet."
        )
        return

    async def pre_checkout_query_handler(self, update):
        """Handle Telegram `pre_checkout_query` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this pre_checkout_query handler does nothing yet."
        )
        return

    async def poll_handler(self, update):
        """Handle Telegram `poll` update."""
        logging.info(
            f"The following update was received: {update}\n"
            "However, this poll handler does nothing yet."
        )
        return

    async def text_message_handler(self, update):
        """Handle `text` message update."""
        replier, reply = None, None
        text = update['text'].lower()
        user_id = update['from']['id'] if 'from' in update else None
        if user_id in self.individual_text_message_handlers:
            replier = self.individual_text_message_handlers[user_id]
            del self.individual_text_message_handlers[user_id]
        elif text.startswith('/'):  # Command handler
            # A command must always start with the ‘/’ symbol and may not be
            # longer than 32 characters.
            # Commands can use latin letters, numbers and underscores.
            print(text)
            command = re.search(
                r"([A-z_1-9]){1,32}",
                text
            ).group(0)  # Get the first group characters matching pattern
            if command in self.commands:
                replier = self.commands[command]['function']
            elif update['chat']['id'] > 0:
                replier = self.unknown_command_message
        else:  # Check alias and text parsers
            logging.info("#TODO alias and text parsers")
        if replier:
            if asyncio.iscoroutinefunction(replier):
                reply = await replier(update)
            else:
                reply = replier(update)
        if reply:
            if type(reply) is str:
                reply = dict(text=reply)
            try:
                return await self.send_message(update=update, **reply)
            except Exception as e:
                logging.error(
                    f"Failed to handle text message:\n{e}",
                    exc_info=True
                )
        return

    async def audio_message_handler(self, update):
        """Handle `audio` message update."""
        logging.info(
            "A audio message update was received, "
            "but this handler does nothing yet."
        )

    async def document_message_handler(self, update):
        """Handle `document` message update."""
        logging.info(
            "A document message update was received, "
            "but this handler does nothing yet."
        )

    async def animation_message_handler(self, update):
        """Handle `animation` message update."""
        logging.info(
            "A animation message update was received, "
            "but this handler does nothing yet."
        )

    async def game_message_handler(self, update):
        """Handle `game` message update."""
        logging.info(
            "A game message update was received, "
            "but this handler does nothing yet."
        )

    async def photo_message_handler(self, update):
        """Handle `photo` message update."""
        logging.info(
            "A photo message update was received, "
            "but this handler does nothing yet."
        )

    async def sticker_message_handler(self, update):
        """Handle `sticker` message update."""
        logging.info(
            "A sticker message update was received, "
            "but this handler does nothing yet."
        )

    async def video_message_handler(self, update):
        """Handle `video` message update."""
        logging.info(
            "A video message update was received, "
            "but this handler does nothing yet."
        )

    async def voice_message_handler(self, update):
        """Handle `voice` message update."""
        logging.info(
            "A voice message update was received, "
            "but this handler does nothing yet."
        )

    async def video_note_message_handler(self, update):
        """Handle `video_note` message update."""
        logging.info(
            "A video_note message update was received, "
            "but this handler does nothing yet."
        )

    async def contact_message_handler(self, update):
        """Handle `contact` message update."""
        logging.info(
            "A contact message update was received, "
            "but this handler does nothing yet."
        )

    async def location_message_handler(self, update):
        """Handle `location` message update."""
        logging.info(
            "A location message update was received, "
            "but this handler does nothing yet."
        )

    async def venue_message_handler(self, update):
        """Handle `venue` message update."""
        logging.info(
            "A venue message update was received, "
            "but this handler does nothing yet."
        )

    async def poll_message_handler(self, update):
        """Handle `poll` message update."""
        logging.info(
            "A poll message update was received, "
            "but this handler does nothing yet."
        )

    async def new_chat_members_message_handler(self, update):
        """Handle `new_chat_members` message update."""
        logging.info(
            "A new_chat_members message update was received, "
            "but this handler does nothing yet."
        )

    async def left_chat_member_message_handler(self, update):
        """Handle `left_chat_member` message update."""
        logging.info(
            "A left_chat_member message update was received, "
            "but this handler does nothing yet."
        )

    async def new_chat_title_message_handler(self, update):
        """Handle `new_chat_title` message update."""
        logging.info(
            "A new_chat_title message update was received, "
            "but this handler does nothing yet."
        )

    async def new_chat_photo_message_handler(self, update):
        """Handle `new_chat_photo` message update."""
        logging.info(
            "A new_chat_photo message update was received, "
            "but this handler does nothing yet."
        )

    async def delete_chat_photo_message_handler(self, update):
        """Handle `delete_chat_photo` message update."""
        logging.info(
            "A delete_chat_photo message update was received, "
            "but this handler does nothing yet."
        )

    async def group_chat_created_message_handler(self, update):
        """Handle `group_chat_created` message update."""
        logging.info(
            "A group_chat_created message update was received, "
            "but this handler does nothing yet."
        )

    async def supergroup_chat_created_message_handler(self, update):
        """Handle `supergroup_chat_created` message update."""
        logging.info(
            "A supergroup_chat_created message update was received, "
            "but this handler does nothing yet."
        )

    async def channel_chat_created_message_handler(self, update):
        """Handle `channel_chat_created` message update."""
        logging.info(
            "A channel_chat_created message update was received, "
            "but this handler does nothing yet."
        )

    async def migrate_to_chat_id_message_handler(self, update):
        """Handle `migrate_to_chat_id` message update."""
        logging.info(
            "A migrate_to_chat_id message update was received, "
            "but this handler does nothing yet."
        )

    async def migrate_from_chat_id_message_handler(self, update):
        """Handle `migrate_from_chat_id` message update."""
        logging.info(
            "A migrate_from_chat_id message update was received, "
            "but this handler does nothing yet."
        )

    async def pinned_message_message_handler(self, update):
        """Handle `pinned_message` message update."""
        logging.info(
            "A pinned_message message update was received, "
            "but this handler does nothing yet."
        )

    async def invoice_message_handler(self, update):
        """Handle `invoice` message update."""
        logging.info(
            "A invoice message update was received, "
            "but this handler does nothing yet."
        )

    async def successful_payment_message_handler(self, update):
        """Handle `successful_payment` message update."""
        logging.info(
            "A successful_payment message update was received, "
            "but this handler does nothing yet."
        )

    async def connected_website_message_handler(self, update):
        """Handle `connected_website` message update."""
        logging.info(
            "A connected_website message update was received, "
            "but this handler does nothing yet."
        )

    async def passport_data_message_handler(self, update):
        """Handle `passport_data` message update."""
        logging.info(
            "A passport_data message update was received, "
            "but this handler does nothing yet."
        )

    @classmethod
    def set_class_maintenance_message(cls, maintenance_message):
        """Set class maintenance message.

        It will be returned if bot is under maintenance, unless and instance
            `_maintenance_message` is set.
        """
        cls._maintenance_message = maintenance_message

    def set_maintenance_message(self, maintenance_message):
        """Set instance maintenance message.

        It will be returned if bot is under maintenance.
        If instance message is None, default class message is used.
        """
        self._maintenance_message = maintenance_message

    def change_maintenance_status(self, maintenance_message=None, status=None):
        """Put the bot under maintenance or end it.

        While in maintenance, bot will reply to users with maintenance_message
            with a few exceptions.
        If status is not set, it is by default the opposite of the current one.
        Optionally, `maintenance_message` may be set.
        """
        if status is None:
            status = not self.under_maintenance
        assert type(status) is bool, "status must be a boolean value!"
        self._under_maintenance = status
        if maintenance_message:
            self.set_maintenance_message(maintenance_message)
        return self._under_maintenance  # Return new status

    def is_allowed_during_maintenance(self, update):
        """Return True if update is allowed during maintenance.

        An update is allowed if any of the criteria in
            `self.allowed_during_maintenance` returns True called on it.
        """
        for criterion in self.allowed_during_maintenance:
            if criterion(update):
                return True
        return False

    def allow_during_maintenance(self, criterion):
        """Add a criterion to allow certain updates during maintenance.

        `criterion` must be a function taking a Telegram `update` dictionary
            and returning a boolean.
        ```# Example of criterion
        def allow_text_messages(update):
            if 'message' in update and 'text' in update['message']:
                return True
            return False
        ```
        """
        self._allowed_during_maintenance.append(criterion)

    async def handle_update_during_maintenance(self, update):
        """Handle an update while bot is under maintenance.

        Handle all types of updates.
        """
        if (
            'message' in update
            and 'chat' in update['message']
            and update['message']['chat']['id'] > 0
        ):
            return await self.send_message(
                text=self.maintenance_message,
                update=update['message'],
                reply_to_update=True
            )
        elif 'callback_query' in update:
            pass
        elif 'inline_query' in update:
            await self.answer_inline_query(
                update['inline_query']['id'],
                self.maintenance_message,
                cache_time=30,
                is_personal=False,
            )
        return

    @classmethod
    def set_class_authorization_denied_message(csl, message):
        """Set class authorization denied message.

        It will be returned if user is unauthorized to make a request.
        """
        csl._authorization_denied_message = message

    def set_authorization_denied_message(self, message):
        """Set instance authorization denied message.

        If instance message is None, default class message is used.
        """
        self._authorization_denied_message = message

    def set_authorization_function(self, authorization_function):
        """Set a custom authorization_function.

        It should evaluate True if user is authorized to perform a specific
            action and False otherwise.
        It should take update and role and return a Boolean.
        Default authorization_function always evaluates True.
        """
        self.authorization_function = authorization_function

    def set_chat_id_getter(self, getter):
        """Set chat_id getter.

        It must be a function that takes an update and returns the proper
            chat_id.
        """
        assert callable(getter), "Chat id getter must be a function!"
        self.get_chat_id = getter

    @staticmethod
    def get_identifier_from_update_or_user_id(user_id=None, update=None):
        """Get telegram id of user given an update.

        Result itself may be passed as either parameter (for backward
            compatibility).
        """
        identifier = user_id or update
        assert identifier is not None, (
            "Provide a user_id or update object to get a user identifier."
        )
        if isinstance(identifier, dict) and 'from' in identifier:
            identifier = identifier['from']['id']
        assert type(identifier) is int, (
            "Unable to find a user identifier."
        )
        return identifier

    def set_individual_text_message_handler(self, handler,
                                        update=None, user_id=None):
        """Set a custom text message handler for the user.

        Any text message update from the user will be handled by this custom
            handler instead of default handlers for commands, aliases and text.
        Custom handlers last one single use, but they can call this method and
            set themselves as next custom text message handler.
        """
        identifier = self.get_identifier_from_update_or_user_id(
            user_id=user_id,
            update=update
        )
        assert callable(handler), (f"Handler `{handler.name}` is not "
                                   "callable. Custom text message handler "
                                   "could not be set.")
        self.individual_text_message_handlers[identifier] = handler
        return

    def remove_individual_text_message_handler(self,
                                           update=None, user_id=None):
        """Remove a custom text message handler for the user.

        Any text message update from the user will be handled by default
            handlers for commands, aliases and text.
        """
        identifier = self.get_identifier_from_update_or_user_id(
            user_id=user_id,
            update=update
        )
        if identifier in self.individual_text_message_handlers:
            del self.individual_text_message_handlers[identifier]
        return

    async def webhook_feeder(self, request):
        """Handle incoming HTTP `request`s.

        Get data, feed webhook and return and OK message.
        """
        update = await request.json()
        asyncio.ensure_future(
            self.route_update(update)
        )
        return web.Response(
            body='OK'.encode('utf-8')
        )

    async def get_me(self):
        """Get bot information.

        Restart bots if bot can't be got.
        """
        try:
            me = await self.getMe()
            if isinstance(me, Exception):
                raise me
            elif me is None:
                raise Exception('getMe returned None')
            self._name = me["username"]
            self._telegram_id = me['id']
        except Exception as e:
            logging.error(
                f"Information about bot with token {self.token} could not "
                f"be got. Restarting in 5 minutes...\n\n"
                f"Error information:\n{e}"
            )
            await asyncio.sleep(5*60)
            self.__class__.stop(
                65,
                f"Information about bot with token {self.token} could not "
                "be got. Restarting..."
            )

    def setup(self):
        """Make bot ask for updates and handle responses."""
        if not self.webhook_url:
            asyncio.ensure_future(self.get_updates())
        else:
            asyncio.ensure_future(self.set_webhook())
            self.__class__.app.router.add_route(
                'POST', self.webhook_local_address, self.webhook_feeder
            )

    async def close_sessions(self):
        """Close open sessions."""
        for session_name, session in self.sessions.items():
            await session.close()

    async def set_webhook(self, url=None, certificate=None,
                          max_connections=None, allowed_updates=None):
        """Set a webhook if token is valid."""
        # Return if token is invalid
        await self.get_me()
        if self.name is None:
            return
        webhook_was_set = await self.setWebhook(
            url=url, certificate=certificate, max_connections=max_connections,
            allowed_updates=allowed_updates
        )  # `setWebhook` API method returns `True` on success
        webhook_information = await self.getWebhookInfo()
        if webhook_was_set:
            logging.info(
                f"Webhook was set correctly.\n"
                f"Webhook information: {webhook_information}"
            )
        else:
            logging.error(
                f"Failed to set webhook!\n"
                f"Webhook information: {webhook_information}"
            )

    async def get_updates(self, timeout=30, limit=100, allowed_updates=None,
                          error_cooldown=10):
        """Get updates using long polling.

        timeout : int
            Timeout set for Telegram servers. Make sure that connection timeout
            is greater than `timeout`.
        limit : int (1 - 100)
            Max number of updates to be retrieved.
        allowed_updates : List(str)
            List of update types to be retrieved.
            Empty list to allow all updates.
            None to fallback to class default.
        """
        # Return if token is invalid
        await self.get_me()
        if self.name is None:
            return
        # Set custom list of allowed updates or fallback to class default list
        if allowed_updates is None:
            allowed_updates = self.allowed_updates
        await self.deleteWebhook()  # Remove eventually active webhook
        update = None  # Do not update offset if no update is received
        while True:
            updates = await self.getUpdates(
                offset=self._offset,
                timeout=timeout,
                limit=limit,
                allowed_updates=allowed_updates
            )
            if updates is None:
                continue
            elif isinstance(updates, TelegramError):
                logging.error(
                    f"Waiting {error_cooldown} seconds before trying again..."
                )
                await asyncio.sleep(error_cooldown)
                continue
            for update in updates:
                asyncio.ensure_future(self.route_update(update))
            if update is not None:
                self._offset = update['update_id'] + 1

    async def route_update(self, update):
        """Pass `update` to proper method.

        Update objects have two keys:
        - `update_id` (which is used as offset while retrieving new updates)
        - One and only one of the following
            `message`
            `edited_message`
            `channel_post`
            `edited_channel_post`
            `inline_query`
            `chosen_inline_result`
            `callback_query`
            `shipping_query`
            `pre_checkout_query`
            `poll`
        """
        if (
            self.under_maintenance
            and not self.is_allowed_during_maintenance(update)
        ):
            return await self.handle_update_during_maintenance(update)
        for key, value in update.items():
            if key in self.routing_table:
                return await self.routing_table[key](value)
        logging.error(f"Unknown type of update.\n{update}")

    @classmethod
    async def start_app(cls):
        """Start running `aiohttp.web.Application`.

        It will route webhook-received updates and other custom paths.
        """
        assert cls.local_host is not None, "Invalid local host"
        assert cls.port is not None, "Invalid port"
        cls.runner = web.AppRunner(cls.app)
        await cls.runner.setup()
        cls.server = web.TCPSite(cls.runner, cls.local_host, cls.port)
        await cls.server.start()
        logging.info(f"App running at http://{cls.local_host}:{cls.port}")

    @classmethod
    async def stop_app(cls):
        """Close bot sessions and cleanup."""
        for bot in cls.bots:
            await bot.close_sessions()
        await cls.runner.cleanup()

    @classmethod
    def stop(cls, message, final_state=0):
        """Log a final `message`, stop loop and set exiting `code`.

        All bots and the web app will be terminated gracefully.
        The final state may be retrieved to get information about what stopped
            the bots.
        """
        logging.info(message)
        cls.final_state = final_state
        cls.loop.stop()
        return

    @classmethod
    def run(cls, local_host=None, port=None):
        """Run aiohttp web app and all Bot instances.

        Each bot will receive updates via long polling or webhook according to
            its initialization parameters.
        A single aiohttp.web.Application instance will be run (cls.app) on
            local_host:port and it may serve custom-defined routes as well.
        """
        if local_host is not None:
            cls.local_host = local_host
        if port is not None:
            cls.port = port
        for bot in cls.bots:
            bot.setup()
        asyncio.ensure_future(cls.start_app())
        try:
            cls.loop.run_forever()
        except KeyboardInterrupt:
            logging.info("Stopped by KeyboardInterrupt")
        except Exception as e:
            logging.error(f"{e}", exc_info=True)
        finally:
            cls.loop.run_until_complete(cls.stop_app())
        return cls.final_state
