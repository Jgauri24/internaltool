from pydantic import BaseModel


class QualificationResult(BaseModel):
    url: str
    pricing_mentioned: bool = False
    sign_up_mentioned: bool = False
    free_trial_mentioned: bool = False
    book_demo_button: bool = False
    talk_to_sales_button: bool = False
    monthly_traffic: int | None = None
