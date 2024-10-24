# This file is part of NSDotPy, a wrapper around httpx that makes interacting
# with the HTML nationstates.net site legally and efficiently easier.
#
# NSDotPy is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License
# as published by the Free Software Foundation either version
# 3 of the License, or (at your option) any later version.
#
# NSDotPy is distributed in the hope that it will be useful but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with NSDotPy. If not, see <https://www.gnu.org/licenses/>.

# standard library imports
import time  # for ratelimiting and userclick
import logging  # for logging
import logging.config  # for logging configuration
import mimetypes  # for flag and banner uploading

# external library imports
import keyboard  # for the required user input
import httpx  # for http stuff
from bs4 import BeautifulSoup, Tag  # for parsing html and xml
from benedict import benedict

# local imports
from . import valid  # for valid region tags


def canonicalize(string: str) -> str:
    """Converts a string to its canonical form used by the nationstates api.

    Args:
        string (str): The string to convert

    Returns:
        str: The canonical form of the string
    """
    return string.lower().strip().replace(" ", "_")


def uncanonicalize(string: str) -> str:
    """Converts a string into a user friendly form from it's canonical nationstates api form.

    Args:
        string (str): The string to convert

    Returns:
        str: The uncanonicale form of the string
    """
    return string.replace("_", " ").title()


class NSSession:
    def __init__(
        self,
        script_name: str,
        script_version: str,
        script_author: str,
        script_user: str,
        keybind: str = "space",
        link_to_src: str = "",
        logger: logging.Logger | None = None,
    ):
        """A wrapper around httpx that abstracts away
        interacting with the HTML nationstates.net site.
        Focused on legality, correctness, and ease of use.

        Args:
            script_name (str): Name of your script
            script_version (str): Version number of your script
            script_author (str): Author of your script
            script_user (str): Nation name of the user running your script
            keybind (str, optional): Keybind to count as a user click. Defaults to "space".
            link_to_src (str, optional): Link to the source code of your script.
            logger (logging.Logger | None, optional): Logger to use. Will create its own with name "NSDotPy" if none is specified. Defaults to None.
        """
        self.VERSION = "2.3.0"
        # Initialize logger
        if not logger:
            self._init_logger()
        else:
            self.logger = logger
        # Create a new httpx session
        self._session = httpx.Client(
            http2=True, timeout=30
        )  # ns can b slow, 30 seconds is hopefully a good sweet spot
        # Set the user agent to the script name, version, author, and user as recommended in the script rules thread:
        # https://forum.nationstates.net/viewtopic.php?p=16394966&sid=be37623536dbc8cee42d8d043945b887#p16394966
        self._lock: bool = False
        self._set_user_agent(
            script_name, script_version, script_author, script_user, link_to_src
        )
        # Initialize nationstates specific stuff
        self._auth_region = "rwby"
        self.chk: str = ""
        self.localid: str = ""
        self.pin: str = ""
        self.nation: str = ""
        self.region: str = ""
        self.keybind = keybind
        # Make sure the nations in the user agent actually exist
        if not self._validate_nations({script_author, script_user}):
            raise ValueError(
                "One of, or both, of the nations in the user agent do not exist. Make sure you're only including the nation name in the constructor, e.g. 'Thorn1000' instead of 'Devved by Thorn1000'"
            )
        self.logger.info(f"Initialized. Keybind to continue is {self.keybind}.")

    def _validate_shard(self, shard, valid_shards, api):
        """Helper function to validate a single shard."""
        if shard not in valid_shards:
            raise ValueError(f"{shard} is not a valid shard for {api}")

    def _validate_shards(self, api: str, shards: set[str]) -> None:
        """Validates shards for a given API."""
        api_to_shard_set = {
            "nation": valid.NATION_SHARDS
            | valid.PRIVATE_NATION_SHARDS
            | valid.PRIVATE_NATION_SHARDS,
            "region": valid.REGION_SHARDS,
            "world": valid.WORLD_SHARDS,
            "wa": valid.WA_SHARDS,
        }

        valid_shards = api_to_shard_set.get(api)
        if not valid_shards:
            raise ValueError(f"Invalid API type: {api}")

        for shard in shards:
            self._validate_shard(shard, valid_shards, api)

    def _set_user_agent(
        self,
        script_name: str,
        script_version: str,
        script_author: str,
        script_user: str,
        link_to_src: str,
    ):
        self.user_agent = (
            f"{script_name}/{script_version} (by:{script_author}; usedBy:{script_user})"
        )
        if link_to_src:
            self.user_agent = f"{self.user_agent}; src:{link_to_src}"
        self.user_agent = f"{self.user_agent}; Written with NSDotPy/{self.VERSION} (by:Sweeze; src:github.com/sw33ze/NSDotPy)"
        self._session.headers.update({"User-Agent": self.user_agent})

    def _validate_nations(self, nations: set[str]) -> bool:
        """Checks if a list of nations exist using the NationStates API.

        Args:
            nations (set[str]): List of nations to check

        Returns:
            bool: True if all nations in the set exist, False otherwise.
        """
        response = self.api_request("world", shard="nations")
        world_nations = response.nations.split(",")
        # check if all nations in the list exist in the world nations
        return all(canonicalize(nation) in world_nations for nation in nations)

    def _init_logger(self):
        self.logger = logging.getLogger("NSDotPy")
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "f": {
                    "format": "%(asctime)s %(message)s",
                    "datefmt": "%I:%M:%S %p",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "f",
                }
            },
            "loggers": {
                "NSDotPy": {"handlers": ["console"], "level": "INFO"},
                "httpx": {"handlers": ["console"], "level": "ERROR"},
            },
        }
        logging.config.dictConfig(config)

    def _get_auth_values(self, response: httpx.Response):
        # make sure it's actually html first
        if not response.headers["Content-Type"].startswith("text/html"):
            return
        # parse the html
        soup = BeautifulSoup(response.text, "lxml")
        # gathering chk and localid so i dont have to worry about authenticating l8r
        if chk := soup.find("input", {"name": "chk"}):
            self.chk = chk["value"].strip()  # type: ignore
        if localid := soup.find("input", {"name": "localid"}):
            self.localid = localid["value"].strip()  # type: ignore
        if pin := self._session.cookies.get("pin"):
            # you should never really need the pin but just in case i'll store it
            # PAST ME WAS RIGHT, I NEEDED IT FOR THE PRIVATE API!!
            self.pin = pin
        if soup.find("a", {"class": "STANDOUT"}):
            self.region = canonicalize(
                soup.find_all("a", {"class": "STANDOUT"})[1].attrs["href"].split("=")[1]
            )

    def _wait_for_input(self, key: str) -> int:
        """Blocks execution until the user presses a key. Used as the one click = one request action.

        Args:
            key (str): The key to wait for

        Returns:
            int: Userclick parameter, milliseconds since the epoch"""
        keyboard.wait(key)
        # the trigger_on_release parameter is broken on windows
        # because of a bug in keyboard so we have to do this
        while keyboard.is_pressed(key):
            pass
        return int(time.time() * 1000)

    def _get_detag_wfe(self) -> str:
        """Gets the detagged WFE of the region you're in.

        Returns:
            str: The detagged WFE"""
        self.logger.info(f"Getting detag WFE for {self.region}...")
        response = self.request(
            f"https://greywardens.xyz/tools/wfe_index/region={self.region}",
        )
        soup = BeautifulSoup(response.text, "lxml")
        # the safest bet for a detag wfe is the first wfe of the region
        return soup.find_all("pre")[-1].text

    def _validate_fields(self, data: dict):
        max_lengths = {
            "pretitle": 28,
            "slogan": 55,
            "currency": 40,
            "animal": 40,
            "demonym_noun": 44,
            "demonym_adjective": 44,
            "demonym_plural": 44,
        }

        # go through each key in the data dict and make sure they're below the max length
        for key, value in data.items():
            if key not in max_lengths:
                continue
            if len(value) > max_lengths[key]:
                raise ValueError(f"{key} is too long, max length is {max_lengths[key]}")
            if len(value) < 2 and key != "slogan":
                raise ValueError(f"{key} should have a minimum length of 2 characters.")
            # check if pretitle contains any non-alphanumeric characters (except spaces)
            if key == "pretitle" and not value.replace(" ", "").isalnum():
                raise ValueError(
                    "Pretitle should only contain alphanumeric characters or spaces."
                )

    def _wait_for_ratelimit(self, head: dict, constant_rate_limit: bool):
        if "X-Pin" in head:
            self.pin = head["X-Pin"]
        if waiting_time := head.get("Retry-After"):
            self.logger.warning(f"Rate limited. Waiting {waiting_time} seconds.")
            time.sleep(int(waiting_time))
        # slow down requests so we dont hit the rate limit in the first place
        requests_left = int(head["RateLimit-Remaining"])
        if requests_left < 10 or constant_rate_limit:
            seconds_until_reset = int(head["RateLimit-Reset"])
            time.sleep(seconds_until_reset / requests_left)

    def _html_request(
        self, url, data={}, files=None, follow_redirects=False
    ) -> httpx.Response:
        data |= {"chk": self.chk, "localid": self.localid}
        userclick = self._wait_for_input(self.keybind)
        # userclick is the number of milliseconds since the epoch, admin uses this for help enforcing the simultaneity rule
        response = self._session.post(
            f"{url}/userclick={userclick}",
            data=data,
            files=files,
            follow_redirects=follow_redirects,
        )
        if response.status_code >= 400:
            with open("error.html", "w") as f:
                f.write(response.text)
            raise httpx.HTTPError(
                f"Received status code {response.status_code} from {response.url}. Error page saved to error.html."
            )
        self._get_auth_values(response)
        return response

    # --- end private methods --- #

    def refresh_auth_values(self):
        self.logger.info("Refreshing authentication values...")
        response = self.request(
            f"https://www.nationstates.net/page=display_region/region={self._auth_region}",
            data={"theme": "century"},
        )
        self._get_auth_values(response)

    def request(
        self,
        url: str,
        data: dict = {},
        files: dict = {},
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Sends a request to the given url with the given data and files.

        Args:
            url (str): URL to send the request to
            data (dict, optional): Payload to send with the request
            files (dict, optional): Payload to send with requests that upload files

        Returns:
            httpx.Response: The response from the server
        """
        if any(
            banned_page in canonicalize(url)
            for banned_page in {
                "page=telegrams",
                "page=dilemmas",
                "page=compose_telegram",
                "page=store",
                "page=help",
            }
        ):
            raise ValueError(
                "You cannot use a tool to interact with telegrams, issues, getting help, or the store. Read up on the script rules: https://forum.nationstates.net/viewtopic.php?p=16394966#p16394966"
            )
        if "api.cgi" in canonicalize(url):
            # you should be using api_request for api requests
            raise ValueError("You should be using api_request() for api requests.")
        elif "nationstates" in canonicalize(url):
            # do all the things that need to be done for html requests
            if self._lock:
                # if lock is true then we're already in the middle of a
                # request and we're in danger of breaking the simultaneity rule
                # so raise an error
                raise PermissionError(
                    "You're already in the middle of a request. Stop trying to violate simultaneity."
                )
            self._lock = True
            response = self._html_request(url, data, files, follow_redirects)
            self._lock = False
        else:
            # if its not nationstates then just pass the request through
            response = self._session.post(
                url, data=data, follow_redirects=follow_redirects
            )
        return response

    def api_request(
        self,
        api: str,
        *,
        target: str = "",
        shard: str | set[str] = "",
        password: str = "",
        constant_rate_limit: bool = False,
    ) -> benedict:
        """Sends a request to the nationstates api with the given data.

        Args:
            api (str): The api to send the request to. Must be "nation", "region", "world", or "wa"
            target (str, optional): The nation, region, or wa council to target. Required for non-world api requests.
            shard (str, optional): The shard, or shards, you're requesting for. Must be a valid shard for the given api. Only required for world and wa api requests.
            password (str, optional): The password to use for authenticating private api requests. Defaults to "". Not required if already signed in, whether through the api or through the HTML site.
            constant_rate_limit (bool, optional): If True, will always rate limit. If False, will only rate limit when there's less than 10 requests left in the current bucket. Defaults to False.

        Returns:
            benedict: A benedict object containing the response from the server. Acts like a dictionary, with keypath and keylist support.
        """
        # TODO: probably move this responsibility to a third party api library to avoid reinventing the wheel
        # if one exists of sufficient quality thats AGPLv3 compatible
        if api not in {"nation", "region", "world", "wa"}:
            raise ValueError("api must be 'nation', 'region', 'world', or 'wa'")
        if api != "world" and not target:
            raise ValueError("target must be specified for non-world api requests")
        if api in {"wa", "world"} and not shard:
            raise ValueError("shard must be specified for world and wa api requests")
        # end argument validation
        # shard validation
        if type(shard) == str:
            shard = {shard}
        self._validate_shards(api, shard)  # type: ignore
        # end shard validation
        data = {
            "v": "12",
        }
        if api != "world":
            data[api] = target
        if shard:
            data["q"] = "+".join(shard)
        url = "https://www.nationstates.net/cgi-bin/api.cgi"
        if password:
            self._session.headers["X-Password"] = password
        if self.pin:
            self._session.headers["X-Pin"] = self.pin
        # rate limiting section
        response = self._session.post(url, data=data)
        # if the server tells us to wait, wait
        self._wait_for_ratelimit(response.headers, constant_rate_limit)
        response.raise_for_status()
        parsed_response = benedict.from_xml(response.text, keyattr_dynamic=True)
        parsed_response.standardize()
        parsed_response: benedict = parsed_response[api]  # type: ignore
        return parsed_response

    def api_issue(
        self,
        nation: str,
        issue: int,
        option: int,
        password: str = "",
        constant_rate_limit: bool = False,
    ) -> benedict:
        """Answers an issue via the API.

        Args:
            nation (str): The nation to perform the command with.
            issue (int): the ID of the issue.
            option (int): the issue option to choose.
            password (str, optional): The password to use for authenticating private api requests. Defaults to "". Not required if already signed in, whether through the api or through the HTML site.
            constant_rate_limit (bool, optional): If True, will always rate limit. If False, will only rate limit when there's less than 10 requests left in the current bucket. Defaults to False.

        Returns:
            benedict: A benedict object containing the response from the server. Acts like a dictionary, with keypath and keylist support.
        """
        if not (password or self.pin):
            raise ValueError("must specify authentication")
        data = {
            "v": "12",
            "c": "issue",
            "nation": canonicalize(nation),
            "issue": issue,
            "option": option,
        }
        url = "https://www.nationstates.net/cgi-bin/api.cgi"
        if password:
            self._session.headers["X-Password"] = password
        if self.pin:
            self._session.headers["X-Pin"] = self.pin
        # rate limiting section
        response = self._session.get(url, params=data)
        # if the server tells us to wait, wait
        self._wait_for_ratelimit(response.headers, constant_rate_limit)
        response.raise_for_status()
        parsed_response = benedict.from_xml(response.text, keyattr_dynamic=True)
        parsed_response.standardize()
        parsed_response: benedict = parsed_response["nation"]  # type: ignore
        return parsed_response

    def api_command(
        self,
        nation: str,
        command: str,
        data: dict,
        password: str = "",
        mode: str = "",
        constant_rate_limit: bool = False,
    ) -> benedict:
        """Sends a non-issue command to the nationstates api with the given data and password.

        Args:
            nation (str): The nation to perform the command with.
            command (str): The command to perform. Must be "giftcard", "dispatch", "rmbpost"
            data (str, optional): The unique data to send with the parameters of the command; consult the API docs for more information.
            password (str, optional): The password to use for authenticating private api requests. Defaults to "". Not required if already signed in, whether through the api or through the HTML site.
            mode (str, optional): Whether to prepare or to execute the command. If value is given, does one of the two and returns result, if no value is given, does both and returns result of execute.
            constant_rate_limit (bool, optional): If True, will always rate limit. If False, will only rate limit when there's less than 10 requests left in the current bucket. Defaults to False.

        Returns:
            benedict: A benedict object containing the response from the server. Acts like a dictionary, with keypath and keylist support.
        """
        if command not in {"giftcard", "dispatch", "rmbpost"}:
            raise ValueError("command must be 'giftcard', 'dispatch', or 'rmbpost'")
        if not (password or self.pin):
            raise ValueError("must specify authentication")
        if mode not in {"", "prepare", "execute"}:
            raise ValueError("mode must be prepare or execute")
        data["v"] = "12"
        data["nation"] = canonicalize(nation)
        data["c"] = command
        data["mode"] = mode if mode else "prepare"  # if no mode than first prepare
        url = "https://www.nationstates.net/cgi-bin/api.cgi"
        if password:
            self._session.headers["X-Password"] = password
        if self.pin:
            self._session.headers["X-Pin"] = self.pin
        # rate limiting section
        response = self._session.get(url, params=data)
        # if the server tells us to wait, wait
        self._wait_for_ratelimit(response.headers, constant_rate_limit)
        response.raise_for_status()
        parsed_response = benedict.from_xml(response.text, keyattr_dynamic=True)
        parsed_response.standardize()
        parsed_response: benedict = parsed_response["nation"]  # type: ignore
        if mode == "":
            # if no mode was specified earlier, repeat command with execute and token
            data["token"] = parsed_response["success"]
            return self.api_command(nation, command, data, mode="execute")
        else:
            return parsed_response

    def api_giftcard(
        self,
        nation: str,
        card_id: int,
        season: int,
        recipient: str,
        password: str = "",
        constant_rate_limit: bool = False,
    ) -> benedict:
        """Gifts a card using the API.

        Args:
            nation (str): The nation to perform the command with.
            card_id (int): The ID of the card to gift.
            season (int): The season of the card to gift.
            recipient (str): The nation to gift the card to.
            password (str, optional): The password to use for authenticating private api requests. Defaults to "". Not required if already signed in, whether through the api or through the HTML site.
            constant_rate_limit (bool, optional): If True, will always rate limit. If False, will only rate limit when there's less than 10 requests left in the current bucket. Defaults to False.

        Returns:
            benedict: A benedict object containing the response from the server. Acts like a dictionary, with keypath and keylist support.
        """
        data = {"cardid": card_id, "season": season, "to": canonicalize(recipient)}
        return self.api_command(
            nation, "giftcard", data, password, constant_rate_limit=constant_rate_limit
        )

    def api_dispatch(
        self,
        nation: str,
        action: str,
        title: str = "",
        text: str = "",
        category: int = 0,
        subcategory: int = 0,
        dispatchid: int = 0,
        password: str = "",
        constant_rate_limit: bool = False,
    ) -> benedict:
        """Add, edit, or remove a dispatch.

        Args:
            nation (str): The nation to perform the command with.
            action (str): The action to take. Must be "add", "edit", "remove"
            title (str, optional): The dispatch title when adding or editing.
            text (str, optional): The dispatch text when adding or editing.
            category: (int, optional), The category ID when adding or editing.
            subcategory (int, optional): The subcategory ID when adding or editing.
            dispatchid (int, optional): The dispatch ID when editing or removing.
            password (str, optional): The password to use for authenticating private api requests. Defaults to "". Not required if already signed in, whether through the api or through the HTML site.
            constant_rate_limit (bool, optional): If True, will always rate limit. If False, will only rate limit when there's less than 10 requests left in the current bucket. Defaults to False.

        Returns:
            benedict: A benedict object containing the response from the server. Acts like a dictionary, with keypath and keylist support.
        """
        # TODO: maybe consider splitting these three functions?
        # TODO: maybe create enums for category and subcategory
        if action not in {"add", "edit", "remove"}:
            raise ValueError("action must be 'add', 'edit', or 'remove'")
        if action != "remove" and not all({title, text, category, subcategory}):
            raise ValueError("must specify title, text, category, and subcategory")
        if action != "add" and not dispatchid:
            raise ValueError("must specify a dispatch id")

        data = {"dispatch": action}
        if title:
            data["title"] = title
        if text:
            data["text"] = text
        if category:
            data["category"] = category
        if subcategory:
            data["subcategory"] = subcategory
        if dispatchid:
            data["dispatchid"] = dispatchid
        return self.api_command(
            nation, "dispatch", data, password, constant_rate_limit=constant_rate_limit
        )

    def api_rmb(
        self,
        nation: str,
        region: str,
        text: str,
        password: str = "",
        constant_rate_limit: bool = False,
    ) -> benedict:
        """Post a message on the regional message board via the API.

        Args:
            nation (str): The nation to perform the command with.
            region (str): the region to post the message in.
            text (str): the text to post.
            password (str, optional): The password to use for authenticating private api requests. Defaults to "". Not required if already signed in, whether through the api or through the HTML site.
            constant_rate_limit (bool, optional): If True, will always rate limit. If False, will only rate limit when there's less than 10 requests left in the current bucket. Defaults to False.

        Returns:
            benedict: A benedict object containing the response from the server. Acts like a dictionary, with keypath and keylist support.
        """
        data = {"region": region, "text": text}
        return self.api_command(
            nation, "rmbpost", data, password, constant_rate_limit=constant_rate_limit
        )

    def login(self, nation: str, password: str) -> bool:
        """Logs in to the nationstates site.

        Args:
            nation (str): Nation name
            password (str): Nation password

        Returns:
            bool: True if login was successful, False otherwise
        """
        self.logger.info(f"Logging in to {nation}")
        url = f"https://www.nationstates.net/page=display_region/region={self._auth_region}"
        # shoutouts to roavin for telling me i had to have page=display_region in the url so it'd work with a userclick parameter

        data = {
            "nation": canonicalize(nation),
            "password": password,
            "theme": "century",
            "logging_in": "1",
            "submit": "Login",
        }

        response = self.request(url, data)

        soup = BeautifulSoup(response.text, "lxml")
        # checks if the body tag has your nation name in it; if it does, you're logged in
        if not soup.find("body", {"data-nname": canonicalize(nation)}):
            return False

        self.nation = canonicalize(nation)
        return True

    def change_nation_flag(self, flag_filename: str) -> bool:
        """Changes the nation flag to the given image.

        Args:
            flag_filename (str): Filename of the flag to change to

        Returns:
            bool: True if the flag was changed, False otherwise
        """
        self.logger.info(f"Changing flag on {self.nation}")
        # THIS WAS SO FUCKING FRUSTRATING BUT IT WORKS NOW AND IM NEVER TOUCHING THIS BULLSHIT UNLESS NS BREAKS IT AGAIN
        url = "https://www.nationstates.net/cgi-bin/upload.cgi"

        data = {
            "nationname": self.nation,
        }
        files = {
            "file": (
                flag_filename,
                open(flag_filename, "rb"),
                mimetypes.guess_type(flag_filename)[0],
            )
        }

        response = self.request(url, data=data, files=files)

        if "page=settings" in response.headers["location"]:
            self.refresh_auth_values()
            return True
        elif "Just a moment..." in response.text:
            self.logger.warning(
                "Cloudflare blocked you idiot get fucked have fun with that like I had to lmaoooooooooo"
            )
        return False

    def change_nation_settings(
        self,
        *,
        email: str = "",
        pretitle: str = "",
        slogan: str = "",
        currency: str = "",
        animal: str = "",
        demonym_noun: str = "",
        demonym_adjective: str = "",
        demonym_plural: str = "",
        new_password: str = "",
    ) -> bool:
        """Given a logged in session, changes customizable fields and settings of the logged in nation.
        Variables must be explicitly named in the call to the function, e.g. "session.change_nation_settings(pretitle='Join Lily', currency='Join Lily')"

        Args:
            email (str, optional): New email for WA apps.
            pretitle (str, optional): New pretitle of the nation. Max length of 28. Nation must have minimum population of 250 million.
            slogan (str, optional): New Slogan/Motto of the nation. Max length of 55.
            currency (str, optional): New currency of the nation. Max length of 40.
            animal (str, optional): New national animal of the nation. Max length of 40.
            demonym_noun (str, optional): Noun the nation will refer to its citizens as. Max length of 44.
            demonym_adjective (str, optional): Adjective the nation will refer to its citizens as. Max length of 44.
            demonym_plural (str, optional): Plural form of "demonym_noun". Max length of 44.
            new_password (str, optional): New password to assign to the nation.

        Returns:
            bool: True if changes were successful, False otherwise.
        """
        self.logger.info(f"Changing settings on {self.nation}")
        url = "https://www.nationstates.net/template-overall=none/page=settings"

        data = {
            "type": pretitle,
            "slogan": slogan,
            "currency": currency,
            "animal": animal,
            "demonym2": demonym_noun,
            "demonym": demonym_adjective,
            "demonym2pl": demonym_plural,
            "email": email,
            "password": new_password,
            "confirm_password": new_password,
            "update": " Update ",
        }
        # remove keys that have empty values
        data = {k: v for k, v in data.items() if v}
        # make sure everything is following the proper length limits and only contains acceptable characters
        self._validate_fields(data)

        response = self.request(url, data)
        return "Your settings have been successfully updated." in response.text

    def move_to_region(self, region: str, password: str = "") -> bool:
        """Moves the nation to the given region.

        Args:
            region (str): Region to move to
            password (str, optional): Region password, if the region is passworded

        Returns:
            bool: True if the move was successful, False otherwise
        """
        self.logger.info(f"Moving {self.nation} to {region}")
        url = "https://www.nationstates.net/template-overall=none/page=change_region"

        data = {"region_name": region, "move_region": "1"}
        if password:
            data["password"] = password
        response = self.request(url, data)

        if "Success!" in response.text:
            self.region = canonicalize(region)
            return True
        return False

    def vote(self, pollid: str, option: str) -> bool:
        """Votes on a poll.

        Args:
            pollid (str): ID of the poll to vote on, e.g. "199747"
            option (str): Option to vote for (starts at 0)

        Returns:
            bool: True if the vote was successful, False otherwise
        """
        self.logger.info(f"Voting on poll {pollid} with {self.nation}")
        url = f"https://www.nationstates.net/template-overall=none/page=poll/p={pollid}"

        data = {"pollid": pollid, "q1": option, "poll_submit": "1"}
        response = self.request(url, data)

        return "Your vote has been lodged." in response.text

    # below are functions that are related to the WA

    def join_wa(self, nation: str, app_id: str) -> bool:
        """Joins the WA with the given nation.

        Args:
            nation (str): Nation to join the WA with
            app_id (str): ID of the WA application to use

        Returns:
            bool: True if the join was successful, False otherwise
        """
        self.logger.info(f"Joining WA with {nation}")
        url = "https://www.nationstates.net/cgi-bin/join_un.cgi"

        data = {"nation": canonicalize(nation), "appid": app_id.strip()}
        response = self.request(url, data)

        if "?welcome=1" in response.headers["location"]:
            # since we're just getting thrown into a cgi script, we'll have to manually grab authentication values
            self.refresh_auth_values()
            return True
        return False

    def resign_wa(self):
        """Resigns from the WA.

        Returns:
            bool: True if the resignation was successful, False otherwise
        """
        self.logger.info("Resigning from WA")
        url = "https://www.nationstates.net/template-overall=none/page=UN_status"

        data = {"action": "leave_UN", "submit": "1"}
        response = self.request(url, data)

        return "From this moment forward, your nation is on its own." in response.text

    def apply_wa(self, reapply: bool = True) -> bool:
        """Applies to the WA.

        Args:
            reapply (bool, optional): Whether to reapply if you've been sent an application that's still valid. Defaults to True.

        Returns:
            bool: True if the application was successful, False otherwise
        """
        self.logger.info(f"Applying to WA with {self.nation}")
        url = "https://www.nationstates.net/template-overall=none/page=UN_status"

        data = {"action": "join_UN"}
        if reapply:
            data["resend"] = "1"
        else:
            data["submit"] = "1"

        response = self.request(url, data)
        return (
            "Your application to join the World Assembly has been received!"
            in response.text
        )

    def endorse(self, nation: str, endorse: bool = True) -> bool:
        """Endorses the given nation.

        Args:
            nation (str): Nation to endorse
            endorse (bool, optional): True=endorse, False=unendorse. Defaults to True.

        Returns:
            bool: True if the endorsement was successful, False otherwise
        """
        self.logger.info(
            f"{('Unendorsing', 'Endorsing')[endorse]} {nation} with {self.nation}"
        )
        url = "https://www.nationstates.net/cgi-bin/endorse.cgi"

        data = {
            "nation": canonicalize(nation),
            "action": "endorse" if endorse else "unendorse",
        }
        response = self.request(url, data)

        return f"nation={canonicalize(nation)}" in response.headers["location"]

    def clear_dossier(self) -> bool:
        """Clears a logged in nation's dossier.

        Returns:
            bool: Whether it was successful or not
        """

        self.logger.info(f"Clearing dossier on {self.nation}")
        url = "https://www.nationstates.net/template-overall=none/page=dossier"
        data = {"clear_dossier": "1"}
        response = self.request(url, data)

        return "Dossier cleared of nations." in response.text

    def add_to_dossier(self, nations: list[str] | str) -> bool:
        """Adds nations to the logged in nation's dossier.

        Args:
            nations (list[str] | str): List of nations to add, or a single nation

        Returns:
            bool: Whether it was successful or not
        """

        self.logger.info(f"Adding {nations} to dossier on {self.nation}")
        url = "https://www.nationstates.net/dossier.cgi"
        data = {
            "currentnation": canonicalize(self.nation),
            "action_append": "Upload Nation Dossier File",
        }
        files = {
            "file": (
                "dossier.txt",
                "\n".join(nations).strip() if type(nations) is list else nations,
                "text/plain",
            ),
        }
        response = self.request(url, data, files=files)

        self.refresh_auth_values()
        return "appended=" in response.headers["location"]

    def wa_vote(self, council: str, vote: str) -> bool:
        """Votes on the current WA resolution.

        Args:
            council (str): Must be "ga" for general assembly, "sc" for security council.
            vote (str): Must be "for" or "against".

        Returns:
            bool: Whether the vote was successful or not
        """
        self.logger.info(
            f"Voting {vote} on {council.upper()} resolution with {self.nation}"
        )
        if council not in {"ga", "sc"}:
            raise ValueError("council must be 'ga' or 'sc'")
        if vote not in {"for", "against"}:
            raise ValueError("vote must be 'for' or 'against'")
        self.logger.info("Voting on WA resolution")

        url = f"https://www.nationstates.net/template-overall=none/page={council}"
        data = {
            "vote": f"Vote {vote.capitalize()}",
        }
        response = self.request(url, data)

        return "Your vote has been lodged." in response.text

    def refound_nation(self, nation: str, password: str) -> bool:
        """Refounds a nation.

        Args:
            nation (str): Name of the nation to refound
            password (str): Password to the nation

        Returns:
            bool: Whether the nation was successfully refounded or not
        """
        url = "https://www.nationstates.net/template-overall=none/"
        data = {
            "logging_in": "1",
            "restore_password": password,
            "restore_nation": "1",
            "nation": nation,
        }
        response = self.request(url, data=data)
        if response.status_code == 302:
            self.nation = nation
            self.refresh_auth_values()
            return True
        return False

    # methods for region control

    def create_region(
        self,
        region_name: str,
        wfe: str,
        *,
        password: str = "",
        frontier: bool = False,
        executive_delegacy: bool = False,
    ) -> bool:
        """Creates a new region.

        Args:
            region_name (str): Name of the region
            wfe (str): WFE of the region
            password (str, optional): Password to the region. Defaults to "".
            frontier (bool, optional): Whether or not the region is a frontier. Defaults to False.
            executive_delegacy (bool, optional): Whether or not the region has an executive WA delegacy. Defaults to False. Ignored if frontier is True.

        Returns:
            bool: Whether the region was successfully created or not
        """
        self.logger.info(f"Creating new region {region_name}")
        url = "https://www.nationstates.net/template-overall=none/page=create_region"
        data = {
            "page": "create_region",
            "region_name": region_name.strip(),
            "desc": wfe.strip(),
            "create_region": "1",
        }
        if password:
            data |= {"pw": "1", "rpassword": password}
        if frontier:
            data |= {"is_frontier": "1"}
        elif executive_delegacy:
            data |= {"delegate_control": "1"}
        response = self.request(url, data)
        return "Success! You have founded " in response.text

    def upload_to_region(self, type: str, filename: str) -> str:
        """Uploads a file to the current region.

        Args:
            type (str): Type of file to upload. Must be "flag" or "banner".
            filename (str): Name of the file to upload. e.g. "myflag.png"

        Raises:
            ValueError: If type is not "flag" or "banner"

        Returns:
            str: Empty string if the upload failed, otherwise the ID of the uploaded file
        """
        self.logger.info(f"Uploading {filename} to {self.region}")
        if type not in {"flag", "banner"}:
            raise ValueError("type must be 'flag' or 'banner'")
        url = "https://www.nationstates.net/cgi-bin/upload.cgi"
        data = {
            "uploadtype": f"r{type}",
            "page": "region_control",
            "region": self.region,
            "expect": "json",
        }
        files = {
            f"file_upload_r{type}": (
                filename,
                open(filename, "rb"),
                mimetypes.guess_type(filename)[0],
            )
        }
        response = self.request(url, data, files=files)
        return "" if "id" not in response.json() else response.json()["id"]

    def set_flag_and_banner(
        self, flag_id: str = "", banner_id: str = "", flag_mode: str = ""
    ) -> bool:
        """Sets the uploaded flag and/or banner for the current region.

        Args:
            flag_id (str, optional): ID of the flag, uploaded with upload_to_region(). Defaults to "".
            banner_id (str, optional): ID of the banner, uploaded with upload_to_region(). Defaults to "".
            flagmode (str, optional): Must be "flag" which will have a shadow, or "logo" which will not, or "" to not change it. Defaults to "".

        Raises:
            ValueError: If flagmode is not "flag", "logo", or ""

        Returns:
            bool: Whether the change was successful or not
        """
        if flag_mode not in {"flag", "logo", ""}:
            raise ValueError("flagmode must be 'flag', 'logo', or ''")
        self.logger.info(f"Setting flag and banner for {self.region}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {
            "newflag": flag_id,
            "newbanner": banner_id,
            "saveflagandbannerchanges": "1",
            "flagmode": flag_mode,
        }
        # remove entries with empty values
        data = {k: v for k, v in data.items() if v}

        response = self.request(url, data)

        return "Regional banner/flag updated!" in response.text

    def change_wfe(self, wfe: str = "") -> bool:
        """Changes the WFE of the current region.

        Args:
            wfe (str, optional): World Factbook Entry to change to. Defaults to the oldest WFE the region has, for detags.

        Returns:
            bool: True if successful, False otherwise
        """
        self.logger.info(f"Changing WFE for {self.region}")
        if not wfe:
            wfe = self._get_detag_wfe()  # haku im sorry for hitting your site so much
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {
            "message": wfe.encode("iso-8859-1", "xmlcharrefreplace")
            .decode()
            .strip(),  # lol.
            "setwfebutton": "1",
        }
        response = self.request(url, data)
        return "World Factbook Entry updated!" in response.text

    # methods for embassies

    def request_embassy(self, target: str) -> bool:
        """Requests an embassy with a region.

        Args:
            target (str): The region to request the embassy with.

        Returns:
            bool: Whether the request was successfully sent or not
        """
        self.logger.info(f"Requesting embassy with {target}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {
            "requestembassyregion": target,
            "requestembassy": "1",  # it's silly that requesting needs this but not closing, aborting, or cancelling
        }
        response = self.request(url, data)
        return "Your proposal for the construction of embassies with" in response.text

    def close_embassy(self, target: str) -> bool:
        """Closes an embassy with a region.

        Args:
            target (str): The region with which to close the embassy.

        Returns:
            bool: Whether the embassy was successfully closed or not
        """
        self.logger.info(f"Closing embassy with {target}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {"cancelembassyregion": target}
        response = self.request(url, data)
        return " has been scheduled for demolition." in response.text

    def abort_embassy(self, target: str) -> bool:
        """Aborts an embassy with a region.

        Args:
            target (str): The region with which to abort the embassy.

        Returns:
            bool: Whether the embassy was successfully aborted or not
        """
        self.logger.info(f"Aborting embassy with {target}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {"abortembassyregion": target}
        response = self.request(url, data)
        return " aborted." in response.text

    def cancel_embassy(self, target: str) -> bool:
        """Cancels an embassy with a region.

        Args:
            target (str): The region with which to cancel the embassy.

        Returns:
            bool: Whether the embassy was successfully cancelled or not
        """
        self.logger.info(f"Cancelling embassy with {target}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {"cancelembassyclosureregion": target}
        response = self.request(url, data)
        return "Embassy closure order cancelled." in response.text

    # end methods for embassies

    def tag(self, action: str, tag: str) -> bool:
        """Adds or removes a tag to the current region.

        Args:
            action (str): The action to take. Must be "add" or "remove".
            tag (str): The tag to add or remove.

        Raises:
            ValueError: If action is not "add" or "remove", or if tag is not a valid tag.

        Returns:
            bool: Whether the tag was successfully added or removed
        """
        if action not in {"add", "remove"}:
            raise ValueError("action must be 'add' or 'remove'")
        if canonicalize(tag) not in valid.REGION_TAGS:
            raise ValueError(f"{tag} is not a valid tag")
        self.logger.info(f"{action.capitalize()}ing tag {tag} for {self.region}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {
            f"{action}_tag": canonicalize(tag),
            "updatetagsbutton": "1",
        }
        response = self.request(url, data)
        return "Region Tags updated!" in response.text

    def eject(self, nation: str) -> bool:
        """Ejects a nation from the current region. Note that a 1 second delay is required before ejecting another nation.

        Args:
            nation (str): The nation to eject.

        Returns:
            bool: Whether the nation was successfully ejected or not
        """
        self.logger.info(f"Ejecting {nation} from {self.region}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {"nation_name": nation, "eject": "1"}
        response = self.request(url, data)
        return "has been ejected from " in response.text

    def banject(self, nation: str) -> bool:
        """Bans a nation from the current region. Note that a 1 second delay is required before banjecting another nation.

        Args:
            nation (str): The nation to banject.

        Returns:
            bool: Whether the nation was successfully banjected or not
        """
        self.logger.info(f"Banjecting {nation} from {self.region}")
        url = "https://www.nationstates.net/template-overall=none/page=region_control/"
        data = {"nation_name": nation, "ban": "1"}
        response = self.request(url, data)
        return "has been ejected and banned from " in response.text

    # end methods for region control

    def junk_card(self, id: str, season: str) -> bool:
        """Junks a card from the current nation's deck.
        Args:
            id (str): ID of the card to junk
            season (str): Season of the card to junk
        Returns:
            bool: Whether the card was successfully junked or not
        """
        self.logger.info(f"Junking card {id} from season {season}")
        url = "https://www.nationstates.net/template-overall=none/page=deck"

        data = {"page": "ajax3", "a": "junkcard", "card": id, "season": season}
        response = self.request(url, data)

        return "Your Deck" in response.text

    def open_pack(self) -> bool:
        """Opens a card pack.

        Returns:
            bool: Whether the bid was successfully removed or not
        """
        self.logger.info("Opening trading card pack")
        url = "https://www.nationstates.net/template-overall=none/page=deck"
        data = {"open_loot_box": "1"}
        response = self.request(url, data)
        return "Tap cards to reveal..." in response.text

    def ask(self, price: str, card_id: str, season: str) -> bool:
        """Puts an ask at price on a card in a season

        Args:
            price (str): Price to ask
            card_id (str): ID of the card
            season (str): Season of the card

        Returns:
            bool: Whether the ask was successfully lodged or not
        """
        self.logger.info(f"Asking for {price} on {card_id} season {season}")
        url = f"https://www.nationstates.net/template-overall=none/page=deck/card={card_id}/season={season}"

        data = {"auction_ask": price, "auction_submit": "ask"}
        response = self.request(url, data)
        return f"Your ask of {price} has been lodged." in response.text

    def bid(self, price: str, card_id: str, season: str) -> bool:
        """Places a bid on a card in a season

        Args:
            price (str): Amount of bank to bid
            card_id (str): ID of the card
            season (str): Season of the card

        Returns:
            bool: Whether the bid was successfully lodged or not
        """
        self.logger.info(f"Putting a bid for {price} on {card_id} season {season}")
        url = f"https://www.nationstates.net/template-overall=none/page=deck/card={card_id}/season={season}"

        data = {"auction_bid": price, "auction_submit": "bid"}
        response = self.request(url, data)

        return f"Your bid of {price} has been lodged." in response.text

    def remove_ask(self, price: str, card_id: str, season: str) -> bool:
        """Removes an ask on card_id in season at price

        Args:
            price (str): Price of the ask to remove
            card_id (str): ID of the card
            season (str): Season of the card

        Returns:
            bool: Whether the ask was successfully removed or not
        """

        self.logger.info(f"removing an ask for {price} on {card_id} season {season}")
        url = f"https://www.nationstates.net/template-overall=none/page=deck/card={card_id}/season={season}"

        data = {"new_price": price, "remove_ask_price": price}
        response = self.request(url, data)
        return f"Removed your ask for {price}" in response.text

    def remove_bid(self, price: str, card_id: str, season: str) -> bool:
        """Removes a bid on a card

        Args:
            price (str): Price of the bid to remove
            card_id (str): ID of the card
            season (str): Season of the card

        Returns:
            bool: Whether the bid was successfully removed or not
        """

        self.logger.info(f"Removing a bid for {price} on {card_id} season {season}")
        url = f"https://www.nationstates.net/template-overall=none/page=deck/card={card_id}/season={season}"

        data = {"new_price": price, "remove_bid_price": price}
        response = self.request(url, data)

        return f"Removed your bid for {price}" in response.text

    def expand_deck(self, price: str) -> bool:
        """Upgrades deck capacity

        Args:
            price (str): Price of the Upgrade

        Returns:
            bool: Whether the upgrade was successfully removed or not
        """

        self.logger.info(f"Upgrading your deck at a cost of {price}")
        url = "https://www.nationstates.net/template-overall=none/page=deck"

        data = {"embiggen_deck": price}
        response = self.request(url, data)

        return "Increased deck capacity from" in response.text

    def add_to_collection(self, card_id: str, card_season: str, collection_id: str):
        """Adds a card to collection_id

        Args:
            card_id (str): Card ID
            card_season (str): Cards season
            collection_id (str): The ID of the collection you want to add to

        Returns:
            bool: Whether the adding was successfully added or not
        """
        self.logger.info(f"Adding {card_id} of season {card_season} to {collection_id}")
        url = f"https://www.nationstates.net/template-overall=none/page=deck/card={card_id}/season={card_season}"

        data = {
            "manage_collections": "1",
            "modify_card_in_collection": "1",
            f"collection_{collection_id}": "1",
            "save_collection": "1",
        }
        response = self.request(url, data)

        return "Updated collections." in response.text

    def remove_from_collection(
        self, card_id: str, card_season: str, collection_id: str
    ):
        """Removes a card from collection_id

        Args:
            card_id (str): Card ID
            card_season (str): Cards season
            collection_id (str): The ID of the collection you want to remove from

        Returns:
            bool: Whether the removal was successfully added or not
        """
        self.logger.info(
            f"Removing {card_id} of season {card_season} from {collection_id}"
        )
        url = f"https://www.nationstates.net/template-overall=none/page=deck/card={card_id}/season={card_season}"

        data = {
            "manage_collections": "1",
            "modify_card_in_collection": "1",
            "start": "0",
            f"collection_{collection_id}": "0",
            "save_collection": "1",
        }
        response = self.request(url, data)

        return "Updated collections." in response.text

    def create_collection(self, name: str):
        """Creates a collection named name

        Args:
            name (str): The name of the collection you want to create

        Returns:
            bool: Whether the creating was successfully added or not
        """
        self.logger.info(f"Creating {name} collection")
        url = "https://www.nationstates.net/template-overall=none/page=deck"

        data = {"edit": "1", "collection_name": name, "save_collection": "1"}
        response = self.request(url, data)

        return "Created collection!" in response.text

    def delete_collection(self, name: str):
        """Deletes a collection named name

        Args:
            name (str): The name of the collection you want to delete

        Returns:
            bool: Whether the deleting was successfully added or not
        """
        self.logger.info(f"Deleting {name} collection")
        url = "https://www.nationstates.net/template-overall=none/page=deck"

        data = {"edit": "1", "collection_name": name, "delete_collection": "1"}
        response = self.request(url, data)

        return "Created collection!" in response.text

    def can_nation_be_founded(self, name: str):
        """Checks if a nation can be founded

        Args:
            name (str): The name of the nation you want to check

        Returns:
            bool: Whether the nation can be founded or not
        """
        self.logger.info(f"Checking {name} in boneyard")
        url = "https://www.nationstates.net/template-overall=none/page=boneyard"

        data = {"nation": name, "submit": "1"}
        response = self.request(url, data)

        return (
            "Available! This name may be used to found a new nation." in response.text
        )

    def join_nday_faction(self, id: str):
        """Joins a faction in the N-Day event

        Args:
            id (str): The ID of the faction you want to join

        Returns:
            bool: Whether the joining was successful or not
        """
        self.logger.info(f"Joining faction {id}")

        url = (
            f"https://www.nationstates.net/template-overall=none/page=faction/fid={id}"
        )
        data = {"join_faction": "1"}
        response = self.request(url, data)

        return " has joined " in response.text

    def leave_nday_faction(self, id: str):
        """Leaves a faction in the N-Day event

        Args:
            id (str): The ID of the faction you want to leave

        Returns:
            bool: Whether the leaving was successful or not
        """
        self.logger.info(f"Leaving faction {id}")

        url = (
            f"https://www.nationstates.net/template-overall=none/page=faction/fid={id}"
        )
        data = {"leave_faction": "1"}
        response = self.request(url, data)

        return " has left " in response.text


if __name__ == "__main__":
    print("this is a module/library, not a script")
