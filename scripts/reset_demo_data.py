"""Reset the demo: delete every processed PO and its file, keep the admin settings, then
upload the four synthetic sample POs again so the read-only demo shows fresh results.

Run from the repo root with the venv active:   python scripts/reset_demo_data.py
It asks you to type RESET before deleting anything. Cost: about $0.03 of AI processing.
"""

from pathlib import Path

import boto3

REGION = "us-east-2"
STACK = "PoIntelligenceStack"
KEEP_PREFIX = "CONFIG#"            # admin settings and their history are kept
SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "synthetic"


def outputs() -> dict:
    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}


def main():
    out = outputs()
    table = boto3.resource("dynamodb", region_name=REGION).Table(out["PoTableName"])
    bucket = boto3.resource("s3", region_name=REGION).Bucket(out["PoBucketName"])

    items, kwargs = [], {"ProjectionExpression": "po_id"}
    while True:
        page = table.scan(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    to_delete = [i["po_id"] for i in items if not i["po_id"].startswith(KEEP_PREFIX)]
    files = list(bucket.objects.filter(Prefix="incoming/"))

    print(f"Will delete {len(to_delete)} table rows (POs, ERP markers, daily counters) "
          f"and {len(files)} uploaded files. Admin settings are kept.")
    if input("Type RESET to continue: ").strip() != "RESET":
        print("Nothing deleted.")
        return

    with table.batch_writer() as batch:
        for po_id in to_delete:
            batch.delete_item(Key={"po_id": po_id})
    for obj in files:
        obj.delete()
    print("Deleted. Uploading the four sample POs...")

    for pdf in sorted(SAMPLES.glob("*.pdf")):
        bucket.upload_file(str(pdf), f"incoming/{pdf.name}", ExtraArgs={"ContentType": "application/pdf"})
        print(f"  uploaded {pdf.name}")
    print("Done. Results appear in the app in about 30 to 60 seconds.")


if __name__ == "__main__":
    main()
