from typing import Type
import asyncio
import os
from datetime import datetime, timedelta, timezone
from mautrix.client import InternalEventType, MembershipEventDispatcher, SyncStream
from mautrix.types import StateEvent, RoomID, UserID
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin
from maubot.handlers import event
import requests

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("rooms")
        helper.copy("message")
        helper.copy("notification_room")
        helper.copy("notification_message")
        helper.copy("invite_message")
        helper.copy("sso_details")

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

    async def create_invite(self, name: str, expires=None) -> str:
        self.log.debug("Creating an invite link")
        sso_details = self.config["sso_details"]
        API_URL = sso_details["API_URL"]
        AUTHENTIK_API_TOKEN = sso_details["AUTHENTIK_API_TOKEN"]
        headers = {
            "Authorization": f"Bearer {AUTHENTIK_API_TOKEN}",
            "Content-Type": "application/json"
        }

        current_time_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')
        if not name:
            name = current_time_str
        else:
            name = f"{name}-{current_time_str}"
        
        if expires is None:
            expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        
        data = {
            "name": name,
            "expires": expires,
            "fixed_data": {},
            "single_use": True,
            "flow": "41a44b0e-1d06-4551-9ec1-41bd793b6f27"  # Replace with the actual flow ID if needed
        }
        
        url = f"{API_URL}/stages/invitation/invitations/"
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 403:
            self.log.error(f"403 Forbidden Error: Check if the API token has the necessary permissions to access {url}")
        response.raise_for_status()
        return response.json()['pk']

    @event.on(InternalEventType.JOIN)
    async def greet(self, evt: StateEvent) -> None:
        self.log.debug(f"User {evt.sender} joined room {evt.room_id}")
        if evt.room_id in self.config["rooms"]:
            if evt.source & SyncStream.STATE:
                self.log.debug("Ignoring state event")
                return
            else:
                self.log.debug("Starting invite creation in the background")
                invite_task = asyncio.create_task(self.create_invite(f"invite-{evt.sender}"))
                
                self.log.debug("Waiting 5 seconds before sending the welcome message")
                await asyncio.sleep(5)

                nick = self.client.parse_user_id(evt.sender)[0]
                user_link = f'<a href="https://matrix.to/#/{evt.sender}">{nick}</a>'
                room_link = f'<a href="https://matrix.to/#/{evt.room_id}">{evt.room_id}</a>'
                msg = self.config["message"].format(user=user_link)
                self.log.debug(f"Formatted welcome message: {msg}")
                await self.send_if_member(evt.room_id, msg)
                
                if self.config["notification_room"]:
                    self.log.debug("Sending notification to room")
                    roomnamestate = await self.client.get_state_event(evt.room_id, 'm.room.name')
                    roomname = roomnamestate.get('name', evt.room_id)  # Use room_id if name is not available
                    notification_message = self.config['notification_message'].format(user=user_link, room=room_link)
                    self.log.debug(f"Formatted notification message: {notification_message}")
                    await self.send_if_member(RoomID(self.config["notification_room"]), notification_message)
                
                self.log.debug("Waiting for the invite link to be created")
                invite_id = await invite_task
                sso_details = self.config["sso_details"]
                base_domain = sso_details["API_URL"].split("//")[1].split("/")[0]
                invite_link = f"https://sso.{base_domain}/if/flow/simple-enrollment-flow/?itoken={invite_id}"
                invite_message = self.config["invite_message"].format(user=nick, invite_link=invite_link)
                self.log.debug(f"Formatted invite message with link: {invite_message}")
                await self.send_direct_message(evt.sender, invite_message)

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config