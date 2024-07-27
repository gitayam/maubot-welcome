from typing import Awaitable, Type, Optional, Tuple
import json
import time
import asyncio

from mautrix.client import Client, InternalEventType, MembershipEventDispatcher, SyncStream
from mautrix.types import (Event, StateEvent, EventID, UserID, EventType,
                            RoomID, RoomAlias, ReactionEvent, RedactionEvent)
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin, MessageEvent
from maubot.handlers import command, event


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

    async def send_if_member(self, room_id: RoomID, message: str) -> None:
        try:
            joined_rooms = await self.client.get_joined_rooms()
            if room_id in joined_rooms:
                await self.client.send_notice(room_id, html=message)
            else:
                self.log.error(f"Bot is not a member of the room {room_id}")
        except Exception as e:
            self.log.error(f"Failed to send message to {room_id}: {e}")

    async def send_direct_message(self, user_id: UserID, message: str) -> None:
        try:
            # Check if there is already a direct message room with the user
            existing_dm_rooms = await self.client.get_dm_rooms()
            dm_room_id = None
            for room_id, users in existing_dm_rooms.items():
                if user_id in users:
                    dm_room_id = room_id
                    break
            
            # If no existing DM room, create a new one
            if not dm_room_id:
                response = await self.client.create_room(invitees=[user_id])
                dm_room_id = response.room_id

            await self.client.send_text(dm_room_id, message)
        except Exception as e:
            self.log.error(f"Failed to send direct message to {user_id}: {e}")

    @event.on(InternalEventType.JOIN)
    async def greet(self, evt: StateEvent) -> None:
        if evt.room_id in self.config["rooms"]:
            if evt.source & SyncStream.STATE:
                return
            else:
                # Wait a few seconds before sending the message
                await asyncio.sleep(5)
                
                nick = self.client.parse_user_id(evt.sender)[0]
                pill = '<a href="https://matrix.to/#/{mxid}">{nick}</a>'.format(mxid=evt.sender, nick=nick)
                msg = self.config["message"].format(user=pill) 
                await self.send_if_member(evt.room_id, msg)

                if self.config["notification_room"]:
                    roomnamestate = await self.client.get_state_event(evt.room_id, 'm.room.name')
                    roomname = roomnamestate['name']
                    notification_message = self.config['notification_message'].format(user=evt.sender, room=roomname)
                    await self.send_if_member(RoomID(self.config["notification_room"]), notification_message)
                
                # Send a direct message to the user
                invite_message = self.config["invite_message"].format(user=pill)
                await self.send_direct_message(evt.sender, invite_message)

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config
