import os
import pickle
from specklepy.objects.base import Base

def extract_elements_by_type(elements_list, save_to_path=False):
    """
    Extract and categorize all Speckle elements from the full model.
    Saves full metadata to 'speckle_elements/speckle_metadata.pkl' if save_to_path=True.
    Each element in 'Other' includes its type, id, name, and geometry center or bounding box.
    """
    extracted_data = {
        "Walls": [],
        "Floors": [],
        "Rooms": [],
        "Stairs": [],
        "Doors": [],
        "Other": []
    }

    def extract_obstacle_metadata(obj):
        """Helper to extract position or bbox for collision checks."""
        metadata = {
            "id": getattr(obj, "id", None),
            "type": getattr(obj, "speckle_type", "Unknown"),
            "name": getattr(obj, "name", None)
        }

        # Option 1: Try to get transform position (used by instances)
        if hasattr(obj, "transform") and hasattr(obj.transform, "matrix"):
            m = obj.transform.matrix
            if isinstance(m, list) and len(m) >= 16:
                metadata["center"] = {
                    "x": float(m[3]) / 1000,
                    "y": float(m[7]) / 1000,
                    "z": float(m[11]) / 1000
                }

        # Option 2: Try to get bounding box if available
        elif hasattr(obj, "bbox"):
            bbox = obj.bbox
            metadata["bbox"] = {
                "min": {
                    "x": float(getattr(bbox, "x", 0)) / 1000,
                    "y": float(getattr(bbox, "y", 0)) / 1000,
                    "z": float(getattr(bbox, "z", 0)) / 1000,
                },
                "max": {
                    "x": float(getattr(bbox, "xSize", 0)) / 1000,
                    "y": float(getattr(bbox, "ySize", 0)) / 1000,
                    "z": float(getattr(bbox, "zSize", 0)) / 1000,
                }
            }

        return metadata

    for collection in elements_list:
        if not isinstance(collection, Base):
            continue

        if hasattr(collection, "elements") and isinstance(collection.elements, list):
            for obj in collection.elements:
                obj_type = getattr(obj, "speckle_type", "Unknown")

                if "Wall" in obj_type:
                    extracted_data["Walls"].append(obj)

                    # Extract embedded doors from walls
                    if hasattr(obj, "elements") and isinstance(obj.elements, list):
                        for sub_elem in obj.elements:
                            if isinstance(sub_elem, Base):
                                cat = getattr(sub_elem, "category", "").lower()
                                bic = getattr(sub_elem, "builtInCategory", "").lower()
                                if "door" in cat or "ost_doors" in bic:
                                    extracted_data["Doors"].append(sub_elem)

                elif "Floor" in obj_type:
                    extracted_data["Floors"].append(obj)
                elif "Room" in obj_type:
                    extracted_data["Rooms"].append(obj)
                elif "Stair" in obj_type:
                    extracted_data["Stairs"].append(obj)
                else:
                    obstacle_data = extract_obstacle_metadata(obj)
                    extracted_data["Other"].append(obstacle_data)

    if save_to_path:
        folder_path = "speckle_elements"
        os.makedirs(folder_path, exist_ok=True)
        filepath = os.path.join(folder_path, "speckle_metadata.pkl")
        with open(filepath, "wb") as f:
            pickle.dump(extracted_data, f)
        print(f"✅ Speckle metadata saved to: {filepath}")

    return extracted_data
