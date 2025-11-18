import openai
import requests
import io
import os
import logging
from dotenv import load_dotenv, find_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
import concurrent.futures
import re
import replicate
import base64
from google import genai
from google.genai import types
from openai import OpenAI

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv(find_dotenv(filename="slackbot.env"))
image_bot_token = os.getenv("IMAGE_BOT_TOKEN")
image_app_token = os.getenv("IMAGE_APP_TOKEN")
openAI_api_key = os.getenv("OPENAI_API_KEY")
replicate_api_token = os.getenv("REPLICATE_API_TOKEN")
google_api_key = os.getenv("GOOGLE_API_KEY")

client = WebClient(token=image_bot_token)
app = App(token=image_bot_token)

openai.api_key = openAI_api_key
os.environ["REPLICATE_API_TOKEN"] = replicate_api_token

# --- IMAGE GENERATION PRICING (update as needed) ---
IMAGE_GEN_PRICING = {
    "flux": 0.04,    # Price per black-forest-labs / flux-1.1-pro
    "flux_base": 0.003,  # Price per image using flux schnell
    "dalle": 0.04,   # Price per HD image 1024x1024 image
    "gpt": 0.042,    # Price per medium quality 1024x1024 image
    "gemini": 0.04   # Price per image imagegen 3
}


def make_slack_title_safe(title):
    title = re.sub(r'[\n\t]+', ' ', title)
    title = re.sub(r'[\x00-\x1F\x7F]', '', title)
    title = re.sub(r'<[^>]*>', '', title)
    title = re.sub(r'\s+', ' ', title)
    title = title[:1000]
    return title.strip()


def slack_notify(message, channel_id=None, image_url=None, image_file=None):
    if channel_id is None:
        channel_id = "C07FZ0W13R7"
    try:
        response = client.chat_postMessage(channel=channel_id, text=message)
    except Exception as e:
        logger.warning(f"Error sending message: {str(e)}")

    if image_file:
        try:
            image_file.seek(0)
            response = client.files_upload_v2(
                channel=channel_id,
                file=image_file,
                filename="image.png",
                title="Requested Image"
            )
        except Exception as e:
            logger.warning(f"Error sending image: {str(e)}")
    elif image_url:
        try:
            response = requests.get(image_url)
            response.raise_for_status()
            filename = image_url.split('/')[-1]
            image_data = io.BytesIO(response.content)
            response = client.files_upload_v2(
                channel=channel_id,
                file=image_data,
                filename=filename,
                title="Requested Image"
            )
        except Exception as e:
            logger.warning(f"Error downloading or sending image: {str(e)}")


def pre_process(prompt):
    try:

        client = OpenAI(api_key=openAI_api_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role":
                 "system",
                 "content": "You are a helpful assistant that improves image "
                 "generation prompts. Only output the improved prompt."},
                {"role":
                 "user",
                 "content": f"Improve this image generation prompt. "
                 "RETAIN all information regarding the prompt:\n"
                 f"{prompt}\nONLY output the improved prompt."}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Prompt preprocessing error: {e}")
        return prompt


class ImageGenerator:
    def __init__(self):
        self.raw_prompt = None
        self.preprocessed_prompt = None

    def preprocess_prompt(self, prompt):
        self.raw_prompt = prompt
        processed = pre_process(prompt)
        self.preprocessed_prompt = processed
        return processed is not None

    def generate_image(self, model="flux_base"):
        prompt = self.preprocessed_prompt
        if not prompt:
            logger.warning("Prompt not preprocessed!")
            return None

        if model == "flux_base":
            try:
                replicate_api = replicate.Client(
                    api_token=os.environ["REPLICATE_API_TOKEN"])
                output = replicate_api.run(
                    "black-forest-labs/flux-schnell",
                    input={
                        "prompt": prompt,
                        "num_outputs": 1,
                        "aspect_ratio": "16:9",
                        "output_format": "png",
                        "output_quality": 90
                    }
                )
                return {"type": "url", "data": str(output[0])}
            except Exception as e:
                logger.error(f"Flux-Schnell image error: {e}")
                return None

        elif model == "flux":
            try:
                replicate_api = replicate.Client(
                    api_token=os.environ["REPLICATE_API_TOKEN"])
                output = replicate_api.run(
                    "black-forest-labs/flux-1.1-pro",
                    input={
                        "prompt": prompt,
                        "num_outputs": 1,
                        "aspect_ratio": "16:9",
                        "output_format": "png",
                        "output_quality": 90
                    }
                )
                return {"type": "url", "data": str(output)}
            except Exception as e:
                logger.error(f"Flux image error: {e}")
                return None

        elif model == "dalle":
            try:
                response = openai.images.generate(
                    model="dall-e-3",
                    prompt=prompt,
                    n=1,
                )
                return {"type": "url", "data": response.data[0].url}
            except Exception as e:
                logger.error(f"DALL-E image error: {e}")
                return None

        elif model == "gpt":
            try:
                client = OpenAI(api_key=openAI_api_key)
                result = client.images.generate(
                    model="gpt-image-1",
                    prompt=prompt,
                    n=1,
                    quality="medium",
                    size="1024x1024",

                )
                image_base64 = result.data[0].b64_json
                image_bytes = base64.b64decode(image_base64)
                image_io = io.BytesIO(image_bytes)
                return {"type": "file", "data": image_io}
            except Exception as e:
                logger.error(f"GPT image error: {e}")
                return None

        elif model == "gemini":
            try:
                client = genai.Client(api_key=google_api_key)
                response = client.models.generate_images(
                    model='imagen-3.0-generate-002',
                    prompt=prompt,
                )
                generated_image = response.generated_images[0]
                image_bytes = generated_image.image.image_bytes  # <--- Here!

                image_io = io.BytesIO(image_bytes)
                return {"type": "file", "data": image_io}
            except Exception as e:
                logger.error(f"Gemini image error: {e}")
                return None


# --- CHANGED! Detect -gpt, -dalle, -flux ---


def parse_image_command(text):
    lowered = text.lower()
    method = "flux_base"  # use new default!
    # Check for -gpt, -dalle, -flux, -flux_base, -schnell, -gemini flag
    if "-gpt" in lowered:
        method = "gpt"
        prompt = re.sub(r"-gpt\b", "", text, flags=re.IGNORECASE).strip()
    elif "-dalle" in lowered:
        method = "dalle"
        prompt = re.sub(r"-dalle\b", "", text, flags=re.IGNORECASE).strip()
    elif "-flux_base" in lowered or "-schnell" in lowered:
        # both explicitly select default
        method = "flux_base"
        prompt = re.sub(r"-(flux_base|schnell)\b", "",
                        text, flags=re.IGNORECASE).strip()
    elif "-flux" in lowered:
        method = "flux"   # pro, not default!
        prompt = re.sub(r"-flux\b", "", text, flags=re.IGNORECASE).strip()
    elif "-gemini" in lowered:
        method = "gemini"
        prompt = re.sub(r"-gemini\b", "", text, flags=re.IGNORECASE).strip()
    else:
        prompt = text.strip()
    return method, prompt


# --- CHANGED: Make requestor mentionable ---


def image_callback(user_input, channel_id, user_id):  # NEW param
    model, prompt = parse_image_command(user_input)
    logger.info(f"Image requested with model: {model}")

    # 1. Lookup cost-per-image for model
    cost = IMAGE_GEN_PRICING.get(model, 0.00)
    generator = ImageGenerator()
    preprocess_success = generator.preprocess_prompt(prompt)
    if not preprocess_success:
        slack_notify(
            f"<@{user_id}> Unable to preprocess prompt.", channel_id)
        return

    result = generator.generate_image(model=model)
    if not result:
        slack_notify(f"<@{user_id}> Failed to generate image.", channel_id)
        return

    requester = f"<@{user_id}>"
    message = (f"{generator.preprocessed_prompt}\n"
               f"*Requested by {requester}*\n"
               f"Model: `{model}`. Estimated cost: `${cost:.3f}`")

    if result["type"] == "url":
        slack_notify(message, channel_id, image_url=result["data"])
    elif result["type"] == "file":
        slack_notify(message, channel_id, image_file=result["data"])


# --- Slack handler ---


@app.command("/image")
def handle_image_command(ack, command, say):
    ack()
    try:
        user_input = command['text']
        channel_id = command['channel_id']
        user_id = command['user_id']  # NEW
        requester = f"<@{user_id}>"
        say(f"{requester} requested image with prompt: "
            f"'/image {user_input}'. Processing your request...")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                image_callback, user_input, channel_id, user_id)
            future.add_done_callback(
                lambda x: logger.info("Image callback completed"))
    except Exception as e:
        logger.error(f"Error handling command: {str(e)}")
        say("Sorry, an error occurred while processing your command.")


if __name__ == "__main__":
    handler = SocketModeHandler(app, image_app_token)
    logger.info("Starting the app...")
    handler.start()
