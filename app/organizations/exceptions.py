class DuplicateTaxIdError(Exception):
    def __init__(self, tax_id: str):
        self.tax_id = tax_id
        super().__init__(f"An organization with the ID: '{tax_id} already exists'")