import os
import logging
from dotenv import load_dotenv, find_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import concurrent.futures
import sys

# Add the directory containing chain_analysis.py to the Python path
sys.path.append(os.path.abspath(
    r"C:\Users\dowdt\git_repos\CyclingTestRigDataAnalysis_git\packages"))

# Now you can import it
# Assuming YamlCompare is a class/function in chain_analysis.py
from chain_analysis import YamlCompare

# %% Setup and tokens
# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load the environment variables from the .env file
load_dotenv(find_dotenv(filename="slackbot.env"))

# Get the tokens from the loaded environment variables
plot_bot_token = os.getenv("PLOT_BOT_TOKEN")
lunch_app_token = os.getenv("PLOT_APP_TOKEN")  # App token, NOT bot token

# Initialize slack bot
client = WebClient(token=plot_bot_token)

# Initialize the Slack app
app = App(token=plot_bot_token)


main_compare_force_regen = ["33_01_40"]
main_compare_filters = {
    "types": ["dry", "wet", "wax"],  # "all", "dry", "wax", "wet"
    "chain_numbers": ["15_01_00",  # 00 - Secret blend (of #00, longest)
                      "16_02_00",  # 00 - Secret blend (of #00, best data)
                      "05_01_01",  # 01 - 160f paraffin (endurance)
                      "27_01_02",  # 02 - Rice bran (bad)
                      "18_03_04",  # 04 - Alpha Olefin (efficient)
                      "19_01_19",  # 19 - BW-405 micro (bad)
                      "28_01_11",  # 11 - Fully hydro soy (bad)
                      "10_01_14",  # 14 - XV1 (endurance)
                      "24_01_19",  # 19 - Halo hot wax
                      "25_01_20",  # 20 - Halo drip wax
                      "26_01_21",  # 21 - Alpha Olefin w/additive (same as #04)
                      "11_03_25",  # 25 - muc-off dry (of #25, longest)
                      "11_02_25",  # 25 - muc-off dry (of #25, best shape)
                      "20_01_26",  # 26 - White lightning
                      "02_01_27",  # 27 - mix par, sun, carnaub
                      "11_01_28",  # 28 - finish line dry
                      "00_01_29",  # 29 - shimanon factory lubricant
                      "12_01_30",  # 30 - no lubricant
                      "29_01_32",  # 32 - 155f paraffin
                      "31_01_36",  # 36 - 145f paraffin
                      "30_01_38",  # 38 - Candelilla wax
                      "32_01_39",  # 39 - #04 & #38 blend
                      "33_01_40",  # 40 - SILCA synergetic
                      ],
    "specials": ["none"]}

main_compare_plot_options = {
    "functions": [],
    "auto_color": 0,  # Turn on no distinguish between runs of same lubricant
    "designator_format": ("{chain_number}-{lubricant_reapply:02.0f}- "
                          "#{lubricant_number:02.0f}: {lubricant_name}"),
    "reorder_legend": 1,            # Re-order the legend by teh sort_key
    "sort_key": "split('#')[1][:2]",  # Sort by lube #
    "ncol": 2,
    "fig_size": tuple(x * 0.8 for x in (16, 9)),
}

main_compare_settings = {
    "name": "Main lubricant compare",
    "simulated_conditions_preset": ["flat"],
    "chain_filters": main_compare_filters,
    "plot_options": main_compare_plot_options,
    "force_regen": main_compare_force_regen,
}


def slack_notify(message, channel_id=None, image_path=None):
    """Send a notification message to a Slack channel and optionally upload an image.

    Parameters
    ----------
        message (str): The text of the message to send.
        channel_id (str, optional): The ID of the Slack channel. Defaults to "C07FZ0W13R7".
        image_path (str, optional): Path to a local image to send. Defaults to None.
    """

    if channel_id is None:
        channel_id = "C07FZ0W13R7"  # Default channel

    logger.debug(f"Slack message: {message}, channel_id: {
                 channel_id}, image_path: {image_path}")

    try:
        logger.info("Attempting to send slack message.")
        if image_path:  # Send image file
            try:
                with open(image_path, 'rb') as file_data:
                    response = client.files_upload_v2(
                        channel=channel_id,
                        file=file_data,
                        filename=os.path.basename(image_path),
                        title=message
                    )
                    logger.info(f"Slack File Upload API Response: {response}")

                if response["ok"]:
                    logger.info("Slack message with image sent successfully")
                else:
                    logger.warning("Failed to send slack message with image.")

            except SlackApiError as e:
                logger.warning(f"Error sending image: {e.response['error']}")
            except FileNotFoundError:
                logger.warning(f"File not found: {image_path}")
            except Exception as e:
                logger.warning(f"Error: {str(e)}")

        else:  # Send text message
            response = client.chat_postMessage(
                channel=channel_id,
                text=message
            )
            logger.info(f"Slack API Response: {response}")
            if response["ok"]:
                logger.debug("Slack message sent successfully.")
                logger.info(f"Slack message sent:\n{message}")
            else:
                logger.warning("Failed to send message.")

    except Exception as e:
        logger.warning(f"Error sending message: {str(e)}")


def plot_callback(channel_id):
    logger.info("Running plot callback")
    try:

        # Call the plotting function
        compare = YamlCompare(settings=main_compare_settings)

        compare.calc()

        image_paths = compare.plot_simulated_condition(
            send_only=True)  # Returns the image path

        image_path = image_paths[0]
        logger.info(f"Plot created at: {image_path}")

        # Send the plot to Slack
        if image_path:
            slack_notify("Here's your plot!",
                         channel_id=channel_id, image_path=image_path)
        else:
            slack_notify("Failed to generate the plot.", channel_id=channel_id)

    except Exception as e:
        logger.error(f"Error during plot generation or sending: {e}")
        slack_notify("An error occurred while generating the plot.",
                     channel_id=channel_id)


@app.command("/plot")
def handle_plot_command(ack, command, say):
    ack()
    logger.info("Received /plot command")
    try:
        channel_id = command['channel_id']
        logger.info(f"Plot request received. Processing")
        say("Generating plot. Please wait...")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(plot_callback, channel_id)
            future.add_done_callback(
                lambda x: logger.info("Plotter callback completed"))

    except Exception as e:
        logger.error(f"Error handling /plot command: {str(e)}")
        say("Sorry, an error occurred while processing your plot command.")


# Usage example:
if __name__ == "__main__":
    handler = SocketModeHandler(app, lunch_app_token)  # Use app token here!
    logger.info("Starting the app...")
    handler.start()
