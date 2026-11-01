from tests import test_support  # must be first
import unittest

from scrapers.alser import AlserScraper
from scrapers.base import payment_terms
from scrapers.technodom import TechnodomScraper
from scrapers.tgrad import TgradScraper
from html_soup import make_soup


class PaymentTermsTest(unittest.TestCase):
    def test_installment_needs_no_overpayment(self):
        self.assertEqual(payment_terms(3899990, 162500, 24), {"installment_months": 24})
        self.assertEqual(payment_terms(535990, 25569, 36), {"credit_available": True})

    def test_bad_values_give_nothing(self):
        for args in ((100, None, 12), (100, 0, 12), (0, 10, 12), (100, 10, 1), (100, 10, 99), (100, "x", 12), (1000, 10, 12)):
            with self.subTest(args=args):
                self.assertEqual(payment_terms(*args), {})

    def test_technodom_prefers_installment(self):
        item = {"kaspiInstallmentInfo": {"months": 12, "price": 44666},
                "payment": {"creditTerms": {"amount": 25569, "monthNum": 36}}}
        self.assertEqual(TechnodomScraper._payment(item, 535990), {"installment_months": 12})
        self.assertEqual(TechnodomScraper._payment({"payment": item["payment"]}, 535990), {"credit_available": True})
        self.assertEqual(TechnodomScraper._payment({}, 535990), {})

    def test_alser_badge(self):
        card = make_soup('<div><div class="installment"><span>в рассрочку</span><span>22 082₸ x 24</span></div></div>')
        self.assertEqual(AlserScraper._payment(card, 529968), {"installment_months": 24})
        self.assertEqual(AlserScraper._payment(make_soup("<div></div>"), 529968), {})

    def test_tgrad_button(self):
        card = make_soup("""<div><a data-params='{"PAYMENT":115377,"MONTH":"9"}'></a></div>""")
        self.assertEqual(TgradScraper._payment(card, 900000), {"credit_available": True})
        self.assertEqual(TgradScraper._payment(make_soup("<div></div>"), 900000), {})


if __name__ == "__main__":
    unittest.main()
