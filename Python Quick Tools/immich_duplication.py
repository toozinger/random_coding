from PIL import ImageGrab, Image
import numpy as np
import cv2
import matplotlib.pyplot as plt
import os
from dotenv import load_dotenv
from google import genai
import pyautogui
import re
import time
import keyboard
import matplotlib.pyplot as plt

# ------ CONFIGURATION ------
SCREEN_LEFT = 450
SCREEN_TOP = 150
SCREEN_WIDTH = 1750
SCREEN_HEIGHT = 1200


def find_red_area_above_cards(img: Image.Image, card_bboxes):
    img_np = np.array(img)

    if not card_bboxes:
        print("No cards provided to find_red_area_above_cards.")
        return None
    min_y = min([y for (_, y, _, _) in card_bboxes])
    if min_y <= 0:
        print("Cards start at top edge, no room above for trash button area.")
        return None

    crop_area = img_np[0:min_y, :, :]
    hsv_crop = cv2.cvtColor(crop_area, cv2.COLOR_RGB2HSV)

    lower_red1 = np.array([0, 100, 100])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 100, 100])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv_crop, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv_crop, lower_red2, upper_red2)
    red_mask = cv2.bitwise_or(mask1, mask2)

    kernel = np.ones((5,5), np.uint8)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        print("No red areas found above cards.")
        return None

    largest_contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest_contour)

    min_area_threshold = 500
    if area < min_area_threshold:
        print(f"Largest red area too small: {area}")
        return None

    x, y, w, h = cv2.boundingRect(largest_contour)

    print(f"Found red area (potential trash button) at {(x, y, w, h)} with area {area}")

    return (x, y, w, h)


def card_is_selected(card_image: Image.Image) -> bool:
    img = np.array(card_image.convert("RGB"))
    h = img.shape[0]
    y_start = int(h * 0.8)
    bar = img[y_start:, :, :]

    avg_color = bar.reshape(-1, 3).mean(axis=0)
    blue_rgb = np.array([120, 170, 245])
    gray_rgb = np.array([32, 33, 36])
    avg_color_rgb = avg_color

    blue_dist = np.linalg.norm(avg_color_rgb - blue_rgb)
    gray_dist = np.linalg.norm(avg_color_rgb - gray_rgb)

    print(f"Avg color: {avg_color_rgb}, blue_dist: {blue_dist:.1f}, gray_dist: {gray_dist:.1f}")

    return blue_dist < gray_dist


def parse_album_count(text):
    normalized_text = re.sub(r'\s+', ' ', text.lower()).strip()

    if "not in any album" in normalized_text:
        return 0

    match = re.search(r'in (\d+) album', normalized_text)
    if match:
        return int(match.group(1))

    raise ValueError(f"Could not determine album count from text: {text}")


def contains_scanning_for_album(text):
    """
    Returns True if text contains phrase indicating 'scanning for album' state.
    Uses similar normalization and regex as parse_album_count.
    """
    normalized_text = re.sub(r'\s+', ' ', text.lower()).strip()
    # You can adjust pattern here if needed.
    # Example pattern: detect phrases containing 'scanning for album'
    if re.search(r'scanning for album', normalized_text):
        return True
    return False


def show_image_matplotlib(img_array, title=None, gray=False):
    plt.figure(figsize=(16, 9))
    if gray:
        plt.imshow(img_array, cmap='gray')
    else:
        plt.imshow(img_array)
    if title:
        plt.title(title)
    plt.tight_layout()
    plt.axis('off')
    plt.show()
    try:
        plt.get_current_fig_manager().window.iconify()
    except Exception:
        pass


def take_screenshot(left, top, width, height):
    bbox = (left, top, left + width, top + height)
    img = ImageGrab.grab(bbox)
    return img


def find_card_bounding_boxes_canny(img, debug=False):
    img_np = np.array(img)
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 130, 140, 1, L2gradient=False)
    kernel = np.ones((2,2), np.uint8)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    if debug:
        show_image_matplotlib(edges, title="Edges After Canny and Closing", gray=True)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    if debug:
        contour_vis = img_np.copy()
        cv2.drawContours(contour_vis, contours, -1, (255,0,0), 2)
        show_image_matplotlib(contour_vis, title="All Found Contours")

    rectangles = []
    min_area = 200
    max_area = 0.1 * img_np.shape[0] * img_np.shape[1]

    for cnt in contours:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)

        if len(approx) == 4 and cv2.isContourConvex(approx):
            x, y, w, h = cv2.boundingRect(approx)
            area = w * h
            aspect = w / float(h)

            if (min_area < area < max_area and 0.45 < aspect < 0.95 and w > 100 and h > 150):
                rectangles.append((x, y, w, h))

    if debug:
        vis = img_np.copy()
        for i_rect, (x, y, w, h) in enumerate(rectangles):
            cv2.rectangle(vis, (x, y), (x + w, y + h), (0,255,0), 3)
            cv2.putText(vis, f'{i_rect+1}', (x+10, y+30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
        show_image_matplotlib(vis, title="Detected Card Bounding Boxes (raw)")

    def is_inside(r1, r2, tol=1):
        x1, y1, w1, h1 = r1
        x2, y2, w2, h2 = r2
        return (x1 > x2 - tol and y1 > y2 - tol and
                x1 + w1 < x2 + w2 + tol and y1 + h1 < y2 + h2 + tol)


    filtered_rectangles = []
    for i, r1 in enumerate(rectangles):
        inside_any = False
        for j, r2 in enumerate(rectangles):
            if i != j and is_inside(r1, r2):
                inside_any = True
                break
        if not inside_any:
            filtered_rectangles.append(r1)

    if debug:
        vis = img_np.copy()
        for i_rect, (x, y, w, h) in enumerate(filtered_rectangles):
            cv2.rectangle(vis, (x, y), (x + w, y + h), (0,255,0), 3)
            cv2.putText(vis, f'{i_rect+1}', (x+10, y+30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
        show_image_matplotlib(vis, title="Detected Card Bounding Boxes (Filtered)")

    return sorted(filtered_rectangles, key=lambda r: (r[1], r[0]))


def extract_info_with_gemini(gemini_client, card_pil_img: Image.Image):
    w, h = card_pil_img.size
    card = card_pil_img.crop((0, h * 0.8, w, h)).convert("L")

    prompt_text = (
        "Extract all of the words from the given image. Return only this text."
    )
    contents = [prompt_text, card_pil_img]

    models_to_try = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]

    last_response = None

    for model_name in models_to_try:
        print(f"Sending image to Gemini LLM for analysis with model '{model_name}'...")
        try:
            response = gemini_client.models.generate_content(
                model=model_name,
                contents=contents,
            )
            last_response = response

            # Check for "scanning for album" here **before** parsing album count
            if contains_scanning_for_album(response.text):
                # Indicate scanning state by raising a specific exception
                raise RuntimeError("Scanning for album detected")

            count = parse_album_count(response.text)
            return response.text, count
        except RuntimeError:
            # Propagate scanning detection signal upwards immediately
            raise
        except Exception as e:
            print(f"Attempt with model '{model_name}' failed: {e}")

    if last_response is not None:
        fig, (ax_img, ax_text) = plt.subplots(1, 2, figsize=(12, 4))
        ax_img.imshow(card, cmap='gray')
        ax_img.set_title("Bottom 40% of Card")
        ax_img.axis("off")
        ax_text.axis("off")
        ax_text.set_title("Gemini LLM Output")
        ax_text.text(0, 0.5, last_response.text, wrap=True, fontsize=10)
        plt.tight_layout()
        plt.show()

    raise ValueError("Failed to parse album count from both lite and non-lite Gemini model outputs.")



def display_card_and_gemini_text(card_index: int, card_image: Image.Image, gemini_text: str):
    img_np = np.array(card_image)

    fig, (ax_img, ax_text) = plt.subplots(1, 2, figsize=(14, 7), gridspec_kw={'width_ratios': [3, 2]})

    ax_img.imshow(img_np)
    ax_img.axis("off")
    ax_img.set_title(f"Card {card_index}")

    ax_text.axis("off")
    ax_text.set_title("Gemini LLM Analysis")

    wrapped_text = "\n".join(gemini_text.splitlines())
    ax_text.text(
        0,
        1,
        wrapped_text,
        fontsize=10,
        va='top',
        ha='left',
        wrap=True,
        bbox=dict(facecolor='wheat', alpha=0.5, boxstyle='round,pad=0.5')
    )

    plt.tight_layout()
    plt.show()
    try:
        plt.get_current_fig_manager().window.iconify()
    except Exception:
        pass



    


if __name__ == "__main__":
    plt.close('all')
    load_dotenv()

    debug = 0
    delete = 1
    visual_wait = 0
    visual_wait_time = 5

    google_api_key = os.getenv("GOOGLE_API_KEY")
    genai_client = genai.Client(api_key=google_api_key)
    fail_count = 0
    
    while True:
        print("Starting iteration... Press ESC to stop.")

        pil_img = take_screenshot(SCREEN_LEFT, SCREEN_TOP, SCREEN_WIDTH, SCREEN_HEIGHT)
        print(f"Screenshot size: {pil_img.size}")

        bboxes_contours = find_card_bounding_boxes_canny(pil_img, debug=debug)
        print(f"Found {len(bboxes_contours)} potential cards using contour detection.")
        
        if len(bboxes_contours) == 0:
            print("Cannot find contours")
            fail_count +=1 
            if fail_count > 2:
                break
            else:
                continue
        else:
            fail_count = 0

        trash_button_bbox = find_red_area_above_cards(pil_img, bboxes_contours)

        card_album_counts = []
        scanning_found = False

        for i, (x, y, w, h) in enumerate(bboxes_contours):
            print(f"\n--- Processing Card {i+1} (Region: x={x}, y={y}, w={w}, h={h}) ---")
            card_img = pil_img.crop((x, y, x + w, y + h))

            try:
                extracted_info, count = extract_info_with_gemini(genai_client, card_img)
            except RuntimeError as e:
                if str(e) == "Scanning for album detected":
                    print(f"'scanning for album' detected in Gemini output for Card {i+1}, restarting loop iteration.")
                    scanning_found = True
                    break
                else:
                    show_image_matplotlib(bboxes_contours, title="Edges After Canny and Closing", gray=True)
                    raise
                    

            print(f"Gemini LLM extracted information for Card {i+1}:\n{extracted_info}\n{'-'*80}")

            if debug:
                display_card_and_gemini_text(i + 1, card_img, extracted_info)

            print(f"Parsed album count for Card {i+1}: {count}")
            card_album_counts.append((i, (x, y, w, h), count))

        if scanning_found:
            print("Restarting main loop iteration due to 'scanning for album' status in Gemini output.")
            time.sleep(0.5)
            continue

        if keyboard.is_pressed('esc'):
            print("Escape key pressed, exiting the loop.")
            break

        selected_indices = []
        for i, (x, y, w, h) in enumerate(bboxes_contours):
            card_img = pil_img.crop((x, y, x + w, y + h))
            if card_is_selected(card_img):
                selected_indices.append(i)

        card_album_counts.sort(key=lambda e: e[2], reverse=True)

        if not card_album_counts:
            print("No cards found to process.")
            time.sleep(1)
            if keyboard.is_pressed('esc'):
                print("Escape key pressed, exiting loop.")
                break
            continue

        highest_count = card_album_counts[0][2]

        top_cards = [e for e in card_album_counts if e[2] == highest_count]

        best_card_index = None
        best_bbox = None
        best_count = highest_count

        for idx, bbox, count in top_cards:
            if idx in selected_indices:
                best_card_index, best_bbox = idx, bbox
                print(f"Keeping already selected card {best_card_index + 1} as best among ties.")
                break

        if best_card_index is None:
            best_card_index, best_bbox, best_count = top_cards[0]
            print(f"No top card selected yet; selecting card {best_card_index + 1}.")

        print(f"\nFinal best card: {best_card_index + 1}. Selected indices before update: {selected_indices}")

        if keyboard.is_pressed('esc'):
            print("Escape key pressed, exiting the loop.")
            break        

        for i in selected_indices:
            if i != best_card_index:
                x, y, w, h = bboxes_contours[i]
                click_x = SCREEN_LEFT + x + w // 2
                click_y = SCREEN_TOP + y + h // 2
                print(f"Deselecting card {i+1} at ({click_x},{click_y})")
                pyautogui.moveTo(click_x, click_y)
                pyautogui.click()
                time.sleep(0.20)

        if best_card_index not in selected_indices:
            x, y, w, h = best_bbox
            click_x = SCREEN_LEFT + x + w // 2
            click_y = SCREEN_TOP + y + h // 2
            print(f"Selecting card {best_card_index + 1} at ({click_x},{click_y})")
            pyautogui.moveTo(click_x, click_y)
            pyautogui.click()
            time.sleep(0.20)
        else:
            print("Best card already selected; no need to click.")

        if visual_wait:
            print(f"Applying visual wait for {visual_wait_time}s")
            time.sleep(visual_wait_time)

        if keyboard.is_pressed('esc'):
            print("Escape key pressed, exiting the loop.")
            break

        if trash_button_bbox:
            x, y, w, h = trash_button_bbox
            click_x = SCREEN_LEFT + x + w // 2
            click_y = SCREEN_TOP + y + h // 2
            pyautogui.moveTo(click_x, click_y)
            if delete:
                pyautogui.click()
            time.sleep(0.20)
            print(f"Trash button located at ({click_x}, {click_y}). Ready to click in future.")
        else:
            print("Trash button not found.")

        if keyboard.is_pressed('esc'):
            print("Escape key pressed, exiting the loop.")
            break

        time.sleep(0.20)
