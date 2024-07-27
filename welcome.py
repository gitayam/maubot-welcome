from typing import Type
import asyncio
from mautrix.client import InternalEventType, MembershipEventDispatcher, SyncStream
from mautrix.types import StateEvent, RoomID, UserID
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin
from maubot.handlers import event

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("rooms")
        helper.copy("message")
        helper.copy("notification_room")
        helper.copy("notification_message")
        helper.copy("invite_message")

class Greeter(Plugin):

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.client.add_dispatcher(MembershipEventDispatcher)
        self.log.info("Greeter plugin started")
        self.client.api.TIMEOUT = 300  # Set the timeout for API requests

    async def retry(self, func, *args, retries=3, **kwargs):
        for i in range(retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if i < retries - 1:
                    self.log.warning(f"Retry {i + 1}/{retries} for {func.__name__} due to {e}")
                    await asyncio.sleep(2 ** i)  # Exponential backoff
                else:
                    self.log.error(f"Failed {func.__name__} after {retries} retries: {e}")
                    raise e

    async def send_if_member(self, room_id: RoomID, message: str) -> None:
        try:
            self.log.debug(f"Checking if bot is a member of room {room_id}")
            joined_rooms = await self.retry(self.client.get_joined_rooms)
            self.log.debug(f"Joined rooms: {joined_rooms}")
            if room_id in joined_rooms:
                self.log.debug(f"Bot is a member of room {room_id}, sending message")
                await self.retry(self.client.send_notice, room_id, html=message)
            else:
                self.log.error(f"Bot is not a member of the room {room_id}")
        except Exception as e:
            self.log.error(f"Failed to send message to {room_id}: {e}")

    async def send_direct_message(self, user_id: UserID, message: str) -> None:
        try:
            self.log.debug(f"Creating direct message room with user {user_id}")
            room_id = await self.retry(self.client.create_room, invitees=[user_id], is_direct=True)
            self.log.debug(f"Created direct message room ID: {room_id}")
            await self.retry(self.client.send_text, room_id, message)
            self.log.debug(f"Sent direct message to {user_id}")
        except Exception as e:
            self.log.error(f"Failed to send direct message to {user_id}: {e}")

    @event.on(InternalEventType.JOIN)
    async def greet(self, evt: StateEvent) -> None:
        self.log.debug(f"User {evt.sender} joined room {evt.room_id}")
        if evt.room_id in self.config["rooms"]:
            if evt.source & SyncStream.STATE:
                self.log.debug("Ignoring state event")
                return
            else:
                self.log.debug("Waiting 5 seconds before sending the message")
                await asyncio.sleep(5)
                
                nick = self.client.parse_user_id(evt.sender)[0]
                pill = f'<a href="https://matrix.to/#/{evt.sender}">{nick}</a>'
                msg = self.config["message"].format(user=pill)
                self.log.debug(f"Formatted welcome message: {msg}")
                await self.send_if_member(evt.room_id, msg)

                if self.config["notification_room"]:
                    self.log.debug(f"Sending notification to room {self.config['notification_room']}")
                    roomnamestate = await self.client.get_state_event(evt.room_id, 'm.room.name')
                    roomname = roomnamestate['name']
                    notification_message = self.config['notification_message'].format(user=evt.sender, room=roomname)
                    self.log.debug(f"Formatted notification message: {notification_message}")
                    await self.send_if_member(RoomID(self.config["notification_room"]), notification_message)
                
                invite_message = self.config["invite_message"].format(user=pill)
                self.log.debug(f"Formatted invite message: {invite_message}")
                await self.send_direct_message(evt.sender, invite_message)

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config