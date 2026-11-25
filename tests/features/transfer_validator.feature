Feature: Transfer order validation
  The AI proposes, the code validates and a person decides. No proposal becomes
  a pending order unless it passes every rule.

  Background:
    Given a dealer "D001" with room for 5 units
    And a dealer "D002" with room for 2 units
    And a unit "8AFZZZ12345670001" in the yard of "D001"

  Scenario: A valid transfer waits for a person
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then a pending order from "D001" to "D002" is created
    And unit "8AFZZZ12345670001" has not moved from "D001"

  Scenario: A unit of the distribution center can be assigned to a dealer
    Given a unit "8AFZZZ12345670002" at the distribution center
    When the agent proposes moving "8AFZZZ12345670002" to "D002"
    Then a pending order from the distribution center to "D002" is created

  Scenario: The VIN is matched whatever its case and spaces
    When the agent proposes moving "  8afzzz12345670001 " to "D002"
    Then a pending order from "D001" to "D002" is created

  Scenario Outline: The agent makes up a VIN
    When the agent proposes moving "<vin>" to "D002"
    Then the proposal is rejected because of "unit_exists"
    And no order is created

    Examples:
      | vin               |
      | 8AFZZZ99999999999 |
      | NOT-A-VIN         |

  Scenario Outline: Only units in a yard can be transferred
    Given unit "8AFZZZ12345670001" is <status>
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then the proposal is rejected because of "unit_available"
    And no order is created

    Examples:
      | status     |
      | in_transit |
      | reserved   |
      | sold       |
      | delivered  |

  Scenario: The agent makes up a dealer
    When the agent proposes moving "8AFZZZ12345670001" to a dealer that does not exist
    Then the proposal is rejected because of "destination_exists"
    And no order is created

  Scenario: The destination is where the unit already is
    When the agent proposes moving "8AFZZZ12345670001" to "D001"
    Then the proposal is rejected because of "different_destination"
    And no order is created

  Scenario: The destination yard is full
    Given 2 units in stock at "D002"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then the proposal is rejected because of "yard_has_room"
    And no order is created

  Scenario: The last place of the yard can be used
    Given 1 units in stock at "D002"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then a pending order from "D001" to "D002" is created

  Scenario: Places promised to pending orders are taken
    Given 1 units in stock at "D002"
    And a unit "8AFZZZ12345670002" in the yard of "D001"
    And a pending order to move "8AFZZZ12345670002" to "D002"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then the proposal is rejected because of "yard_has_room"

  Scenario: A rejected order gives its place back
    Given 1 units in stock at "D002"
    And a unit "8AFZZZ12345670002" in the yard of "D001"
    And a rejected order to move "8AFZZZ12345670002" to "D002"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then a pending order from "D001" to "D002" is created

  Scenario: Places promised at another dealer do not count
    Given a dealer "D003" with room for 5 units
    And 1 units in stock at "D002"
    And a unit "8AFZZZ12345670002" in the yard of "D001"
    And a pending order to move "8AFZZZ12345670002" to "D003"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then a pending order from "D001" to "D002" is created

  Scenario: The unit already waits for a decision
    Given a pending order to move "8AFZZZ12345670001" to "D002"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then the proposal is rejected because of "not_already_proposed"

  Scenario: A rejected order does not block a new proposal
    Given a rejected order to move "8AFZZZ12345670001" to "D002"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then a pending order from "D001" to "D002" is created

  Scenario: Every broken rule is reported at once
    Given unit "8AFZZZ12345670001" is reserved
    And 2 units in stock at "D002"
    When the agent proposes moving "8AFZZZ12345670001" to "D002"
    Then the proposal is rejected because of "unit_available, yard_has_room"
