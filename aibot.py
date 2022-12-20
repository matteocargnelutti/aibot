import json
import os
import logging
import time
from textwrap import dedent

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import openai
from dotenv import load_dotenv

# setup
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
load_dotenv()
app = App()
my_user_id = app.client.auth_test().data["user_id"]
openai.api_key = os.getenv('OPENAI_API_KEY')
OPENAI_PARAMS = {
    'model': "text-davinci-003",
    'temperature': 0.7,
    'max_tokens': 250,
    'top_p': 1,
    'frequency_penalty': 0,
    'presence_penalty': 0,
    # 'stop': ['Bot:', 'You:'],
}
cached_user_names = {}

### helpers ###

def get_text(prompt, **extra_params):
    openai_response_obj = openai.Completion.create(prompt=prompt, **{**OPENAI_PARAMS, **extra_params})
    logger.debug(f"OpenAI response: {openai_response_obj}")
    return openai_response_obj.choices[0].text.strip().strip('"')

def id_to_user_name(user_id):
    if user_id not in cached_user_names:
        user_info = app.client.users_info(user=user_id)
        cached_user_names[user_id] = user_info.data['user']['real_name']
    return cached_user_names[user_id]

def readable_timedelta(seconds):
    # via https://codereview.stackexchange.com/a/245215
    data = {}
    data['days'], remaining = divmod(seconds, 86_400)
    data['hours'], remaining = divmod(remaining, 3_600)
    data['minutes'], data['seconds'] = divmod(remaining, 60)

    time_parts = [f'{round(value)} {name}' for name, value in data.items() if value > 0]
    if time_parts:
        return ' '.join(time_parts)
    else:
        return 'less than 1 second'


### views ###

@app.command("/ai")
def ai(ack, respond, command):
    logger.debug(command)
    ack()

    response_type = "ephemeral"
    prompt = command['text']
    if prompt.split(maxsplit=1)[0] == "say":
        response_type = "in_channel"
        prompt = prompt.split(maxsplit=1)[1]

    formatted_prompt = f"{command['user_name']} asked: /ai {command['text']}"
    response = get_text(prompt)
    attachment = {
        "text": response,
        "callback_id": "public_repost",
        "color": "#3AA3E3",
    }
    # only show "Post publicly" button if message not already public, and we're not in a DM where we can't post
    if response_type == "ephemeral" and command["channel_name"] != "directmessage":
        attachment["actions"] = [{
            "name": "say",
            "text": "Post publicly",
            "type": "button",
            "value": json.dumps({"prompt": formatted_prompt, "response": response}),
        }]
    respond(
        formatted_prompt,
        response_type=response_type,
        attachments=[attachment],
    )

@app.action("public_repost")
def public_repost(ack, payload, respond, say):
    ack()
    to_repost = json.loads(payload['value'])
    say(to_repost["prompt"], response_type="in_channel", attachments=[
        {
            "text": to_repost["response"],
            "color": "#3AA3E3",
        }
    ])
    respond(text='', replace_original=True, delete_original=True)

@app.event("message")
def handle_dm(ack, payload, logger, say):
    """Handle conversations with the app itself."""
    ack()

    # fetch previous slack messages in conversation, and turn into prompts like "<User Name | 3 minutes ago>: message"
    messages = app.client.conversations_history(channel=payload["channel"])
    messages = messages.data["messages"]
    formatted_messages = []
    max_prompt_length = 1000  # characters
    stop_tokens = set()  # list of stop tokens to stop it from generating replies to itself
    for message in messages:
        if message['text'] == "reset":
            # given bare "reset" message, ignore previous messages
            break
        user_name = id_to_user_name(message["user"])
        readable_age = readable_timedelta(time.time() - int(float(message["ts"])))
        formatted_messages.append(f"<{user_name} | {readable_age} ago>: {message['text']}")
        stop_tokens.add(f"<{user_name} |")
    formatted_messages.reverse()
    my_user_name = id_to_user_name(my_user_id)
    stop_tokens.add(f"<{my_user_name} |")

    # fetch OpenAI response
    prompt = dedent(f"""
        This is a conversation with {my_user_name}, a friendly, helpful AI bot.
        
        {' '.join(formatted_messages)[-max_prompt_length:]}
        <{my_user_name} | now>:
    """).strip()
    response = get_text(prompt, stop=list(stop_tokens))

    say(response, response_type="in_channel")

if __name__ == "__main__":
    handler = SocketModeHandler(app)
    handler.start()