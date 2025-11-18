import urllib.request
from PIL import Image, ImageDraw

def download_image(url, filename="recent_aurora.jpg"):
    """
    Downloads an image from a given URL and saves it to a file.

    Args:
        url (str): The URL of the image to download.
        filename (str): The name to save the image as.  Defaults to "recent_aurora.jpg".
    """
    try:
        urllib.request.urlretrieve(url, filename)
        print(f"Image downloaded successfully to {filename}")
        return filename  # Return the filename for further processing
    except Exception as e:
        print(f"Error downloading image: {e}")
        return None

def draw_centered_circle(image_path, outline_color="white", outline_width=2):
    """
    Opens an image, draws a white circle in the center with the maximum possible radius,
    and saves the modified image.

    Args:
        image_path (str): The path to the image file.
        circle_color (str): The color of the circle. Defaults to "white".
    """
    try:
        img = Image.open(image_path)
        width, height = img.size
        draw = ImageDraw.Draw(img)

        # Calculate the radius of the circle (smaller of width/2 and height/2)
        radius = min(width, height) // 2
        
        radius = radius*0.9

        # Calculate the coordinates of the circle's bounding box
        x0 = (width // 2) - radius
        y0 = (height // 2) - radius
        x1 = (width // 2) + radius
        y1 = (height // 2) + radius

        # Draw the circle
        draw.ellipse((x0, y0, x1, y1),  outline=outline_color, width=outline_width)

        # Save the modified image (overwriting the original)
        img.save(image_path)
        print(f"Circle drawn and image saved to {image_path}")

    except FileNotFoundError:
        print(f"Error: Image file not found at {image_path}")
    except Exception as e:
        print(f"Error drawing circle: {e}")


if __name__ == "__main__":
    image_url = "https://auroramax.phys.ucalgary.ca/recent/recent_1080p.jpg?"
    image_file = download_image(image_url)

    if image_file:
        draw_centered_circle(image_file)
